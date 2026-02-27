from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Car(Base):
    __tablename__ = "cars"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512))
    price: Mapped[str] = mapped_column(String(128))
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Car {self.id} {self.title!r}>"


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "offer_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    offer_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(2048))
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Favorite {self.user_id} {self.offer_id}>"


class Request(Base):
    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_name: Mapped[str] = mapped_column(String(256))
    username: Mapped[str | None] = mapped_column(String(256), nullable=True)
    offer_id: Mapped[str] = mapped_column(String(64))
    offer_title: Mapped[str] = mapped_column(String(512))
    offer_url: Mapped[str] = mapped_column(String(2048))
    request_type: Mapped[str] = mapped_column(String(32))  # "order" or "question"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Request {self.id} {self.request_type} {self.user_name}>"


class ManualCar(Base):
    __tablename__ = "manual_cars"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512))
    year: Mapped[str] = mapped_column(String(32))
    mileage: Mapped[str] = mapped_column(String(64))
    fuel: Mapped[str] = mapped_column(String(128), default="")
    engine: Mapped[str] = mapped_column(String(128), default="")
    transmission: Mapped[str] = mapped_column(String(128), default="")
    price: Mapped[str] = mapped_column(String(128), default="")
    auction_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    added_by: Mapped[int] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<ManualCar {self.id} {self.title!r}>"
