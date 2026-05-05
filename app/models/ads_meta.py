from sqlalchemy import Column, ForeignKey, Integer, JSON, String

from app.core.database import Base


class AdsMeta(Base):
    __tablename__ = "ads_meta"

    id = Column(Integer, primary_key=True, index=True)
    playlist_id = Column(
        Integer,
        ForeignKey("playlists.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    category = Column(String, nullable=True)
    genre = Column(String, nullable=True)
    country = Column(String, nullable=True)
    master_playlist = Column(String, nullable=True)
    ads = Column(JSON, nullable=True, default=list)
    color = Column(String, nullable=True)
