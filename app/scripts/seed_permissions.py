"""从 CSV 导入岗位权限和用户数据到 meta 库。首次部署或权限更新时运行。"""
import asyncio, csv, hashlib, os, sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent.parent
CSV_DIR = ROOT / "conf"  # 放 CSV 的地方，可改为绝对路径

sys.path.insert(0, str(ROOT))

from app.clients.mysql_client_manager import meta_mysql_client_manager
from sqlalchemy import text


def hash_pwd(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()


async def seed():
    meta_mysql_client_manager.init()

    async with meta_mysql_client_manager.session_factory() as session:
        # 0. 确保表存在
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS role_permission (
                role_name VARCHAR(64) NOT NULL,
                indicator_group VARCHAR(64),
                indicator_name VARCHAR(128) NOT NULL,
                PRIMARY KEY (role_name, indicator_name)
            )
        """))
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INT PRIMARY KEY AUTO_INCREMENT,
                username VARCHAR(64) UNIQUE NOT NULL,
                password_hash VARCHAR(128) NOT NULL,
                level VARCHAR(32) NOT NULL DEFAULT '普通员工',
                position VARCHAR(64) NOT NULL DEFAULT '综合管理',
                org_name VARCHAR(128),
                allowed_orgs JSON,
                created_at DATETIME DEFAULT NOW()
            )
        """))
        await session.commit()

        # 1. 清空旧权限数据
        await session.execute(text("DELETE FROM role_permission"))
        await session.execute(text("DELETE FROM users"))

        # 2. 导入岗位指标权限
        role_csv = ROOT / "conf" / "岗位指标权限.csv"
        if not role_csv.exists():
            # 尝试从桌面读取
            role_csv = Path(r"C:\Users\10704\Desktop\csv\岗位指标权限.csv")

        with open(role_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader)  # 跳过表头
            count = 0
            for row in reader:
                if len(row) < 3:
                    continue
                role_name = row[0].strip()
                # 统一岗位名：综合管理(行领导) → 综合管理
                if "综合管理" in role_name or "行领导" in role_name or "admin" in role_name:
                    role_name = "综合管理"
                group_name = row[1].strip()
                indicators = [i.strip() for i in row[2].split(",") if i.strip()]
                for ind in indicators:
                    await session.execute(
                        text("INSERT IGNORE INTO role_permission (role_name, indicator_group, indicator_name) VALUES (:r, :g, :i)"),
                        {"r": role_name, "g": group_name, "i": ind}
                    )
                    count += 1
        print(f"岗位权限导入: {count} 条")

        # 3. 导入用户
        user_csv = ROOT / "conf" / "用户权限配置.csv"
        if not user_csv.exists():
            user_csv = Path(r"C:\Users\10704\Desktop\csv\用户权限配置.csv")

        with open(user_csv, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader)
            user_count = 0
            for row in reader:
                if len(row) < 6:
                    continue
                username = row[0].strip()
                password = row[1].strip() if row[1].strip() else "1"
                org_name = row[2].strip()  # 所属机构（如A市）
                role_name = row[3].strip()
                if "综合管理" in role_name or "行领导" in role_name or "admin" in role_name:
                    role_name = "综合管理"
                orgs_raw = row[4].strip()
                indicator_group = row[5].strip()

                # 机构范围：解析 "A市,B市,C市" → JSON数组 ["A市","B市","C市"]
                if "全部" in orgs_raw or len(orgs_raw.split(",")) >= 13:
                    allowed_orgs = '["A市","B市","C市","D市","E市","F市","G市","H市","I市","J市","K市","L市","M市"]'
                else:
                    orgs = [o.strip() for o in orgs_raw.split(",") if o.strip()]
                    import json
                    allowed_orgs = json.dumps(orgs, ensure_ascii=False)

                await session.execute(
                    text("""INSERT INTO users (username, password_hash, level, position, org_name, allowed_orgs)
                            VALUES (:u, :p, '普通员工', :r, :o, :a)
                            ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash), position=VALUES(position), org_name=VALUES(org_name), allowed_orgs=VALUES(allowed_orgs)"""),
                    {"u": username, "p": hash_pwd(password), "r": role_name, "o": org_name, "a": allowed_orgs}
                )
                user_count += 1
        print(f"用户导入: {user_count} 个")

        # 3. 补充计算指标权限
        #    CSV 通常只含直接指标,上面的 DELETE 已把 seed_indicator_formulas 插入的
        #    计算指标权限清掉了。这里重建 indicator_formula 表并重新 seed 计算指标权限,
        #    使 seed_permissions 独立可用、不依赖服务启动(lifespan)来恢复权限。
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS indicator_formula (
                term VARCHAR(64) PRIMARY KEY,
                aliases JSON,
                formula_type VARCHAR(32) NOT NULL DEFAULT 'computed',
                index_names JSON NOT NULL,
                sql_template VARCHAR(500) NOT NULL,
                description VARCHAR(500)
            )
        """))
        await session.commit()
        from app.repositories.mysql.meta_mysql_repository import MetaMysqlRepository
        meta_repo = MetaMysqlRepository(session)
        await meta_repo.seed_indicator_formulas()

        await session.commit()
        print("权限数据导入完成")

    await meta_mysql_client_manager.close()


if __name__ == "__main__":
    asyncio.run(seed())
