"""导出预置数据为 SQL dump,供一键安装时导入。
finance: org_info + index_list + index_data(13万行业务数据)
meta: users + role_permission + indicator_formula(用户/权限/公式,不含审计日志)
"""
import os
import sys

import pymysql
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("DB_HOST", "127.0.0.1")
PORT = int(os.getenv("DB_PORT", "3306"))
USER = os.getenv("DB_USER", "root")
PWD = os.getenv("DB_PASSWORD", "123321")

OUT_DIR = "two_database_update"
os.makedirs(OUT_DIR, exist_ok=True)


def esc(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r")
    return f"'{s}'"


def dump_table(conn, db, table, f, batch_size=200):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {db}.{table}")
    cols = [d[0] for d in cur.description]
    col_list = ",".join(f"`{c}`" for c in cols)
    count = 0
    batch = []
    for row in cur:
        batch.append(f"({','.join(esc(v) for v in row)})")
        count += 1
        if len(batch) >= batch_size:
            f.write(f"INSERT INTO `{table}` ({col_list}) VALUES {','.join(batch)};\n")
            batch = []
    if batch:
        f.write(f"INSERT INTO `{table}` ({col_list}) VALUES {','.join(batch)};\n")
    cur.close()
    print(f"  {db}.{table}: {count} 行")
    return count


def main():
    conn = pymysql.connect(host=HOST, port=PORT, user=USER, password=PWD, charset="utf8mb4")

    # 1. finance 业务数据
    print("导出 finance 业务数据...")
    with open(f"{OUT_DIR}/finance_data.sql", "w", encoding="utf-8") as f:
        f.write("-- finance 业务数据(org_info + index_list + index_data)\n")
        f.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n\n")
        total = 0
        for tbl in ["org_info", "index_list", "index_data"]:
            f.write(f"\n-- {tbl}\n")
            total += dump_table(conn, "finance", tbl, f)
        f.write("\nSET FOREIGN_KEY_CHECKS=1;\n")
    print(f"  finance 总计 {total} 行 → {OUT_DIR}/finance_data.sql")

    # 2. meta 预置数据(只用户+权限+公式,不含审计/日志)
    print("导出 meta 预置数据(用户+权限+公式)...")
    with open(f"{OUT_DIR}/meta_data.sql", "w", encoding="utf-8") as f:
        f.write("-- meta 预置数据(users + role_permission + indicator_formula)\n")
        f.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n\n")
        total = 0
        for tbl in ["users", "role_permission", "indicator_formula"]:
            f.write(f"\n-- {tbl}\n")
            total += dump_table(conn, "meta", tbl, f)
        f.write("\nSET FOREIGN_KEY_CHECKS=1;\n")
    print(f"  meta 总计 {total} 行 → {OUT_DIR}/meta_data.sql")

    conn.close()
    print("\n导出完成!")


if __name__ == "__main__":
    main()
