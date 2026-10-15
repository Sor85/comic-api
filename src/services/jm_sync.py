"""JM favorites sync: folder scopes and the JMComic-kit Pindextitle naming rule."""
from typing import Literal

from pydantic import Field, field_validator

from src.errors import ComicApiError
from src.services.favorite_sync import BaseSyncSettings, FavoriteSync
from src.services.jm_naming import JM_DIRECTORY_RULE
from src.services.local_images import DIRECTORY_RULES, numeric_id


class SyncSettings(BaseSyncSettings):
    folder_ids: list[str] = Field(default_factory=list, max_length=100)
    directory_rule: Literal["id", "title", "title_plain", "author", "Bd_Aauthoroname_Pindextitle"] = "title"

    @field_validator("folder_ids")
    @classmethod
    def valid_folders(cls, values):
        if any(not value.isascii() or not value.isdigit() or len(value) > 20 for value in values):
            raise ValueError("收藏夹编号无效")
        return list(dict.fromkeys(values))


class JmSync(FavoriteSync):
    source = "jm"
    label = "禁漫"
    category = "JM 收藏"
    settings_model = SyncSettings
    roots_env = "JM_DOWNLOAD_ROOTS"
    default_root = "jm-favorites"
    directory_rules = DIRECTORY_RULES

    def comic_id(self, value):
        return numeric_id(value, "jm")

    def scopes(self, config):
        return [{"folder_id": folder_id, "order": "mr"} for folder_id in config["folder_ids"] or ["0"]]

    async def _chapter_data(self, config, album_id, chapter):
        chapter_id = numeric_id(chapter["id"])
        if config["directory_rule"] != JM_DIRECTORY_RULE:
            return await self.service.get_chapter_images("jm", album_id, chapter_id), chapter
        if not hasattr(self.service.source("jm"), "chapter_detail"):
            raise ComicApiError("当前禁漫插件不提供 Pindextitle 所需的原始章节信息", 400, "unsupported", "jm")
        data = await self.service.call("jm", "chapter_detail", album_id, chapter_id)
        if (not isinstance(data, dict) or not {"id", "name", "sort", "series_id", "images"} <= data.keys()
                or int(numeric_id(data["id"])) != int(chapter_id) or not isinstance(data["images"], list)):
            raise ComicApiError("禁漫章节信息不完整或编号不一致", 502, "invalid_response", "jm")
        return data["images"], dict(chapter, name=data["name"], sort=data["sort"], series_id=data["series_id"])
