"""Bika favorites sync: one favorites list per account, 24-hex comic IDs, numeric chapter orders."""
from src.services.favorite_sync import FavoriteSync


class BikaSync(FavoriteSync):
    source = "bika"
    label = "哔咔"
    roots_env = "BIKA_DOWNLOAD_ROOTS"
    default_root = "bika-favorites"
    id_pattern = r"[0-9a-f]{24}"

    def comic_id(self, value):
        return super().comic_id(str(value).lower())

    def normalize_id(self, value):
        return super().normalize_id(value.lower() if isinstance(value, str) else value)

    def scopes(self, config):
        # Bika has no favorite folders; "da" (oldest first) keeps pages stable while new favorites arrive.
        return [{"sort": "da"}]

    catalog_sorts = ("dd", "da", "ld", "vd")

    def catalog_scope(self, sort):
        return {"sort": sort}
