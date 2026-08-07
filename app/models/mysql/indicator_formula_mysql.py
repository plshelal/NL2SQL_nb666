from sqlalchemy import String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base


class IndicatorFormulaMySQL(Base):
    __tablename__ = "indicator_formula"

    term: Mapped[str] = mapped_column(String(64), primary_key=True)
    aliases: Mapped[list | None] = mapped_column(JSON)
    formula_type: Mapped[str] = mapped_column(String(32), default="computed")
    index_names: Mapped[list] = mapped_column(JSON, nullable=False)
    sql_template: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
