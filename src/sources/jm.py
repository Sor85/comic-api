from src.clients.jm import JmClient
from src.sources import SourcePlugin


class JmPlugin(SourcePlugin):
    id = "jm"
    name = "禁漫天堂"
    capabilities = ["search", "tag_search", "multi_tag_search", "author_search", "detail", "pages", "latest", "category", "leaderboard", "random", "login", "favorites", "add_favorite", "remove_favorite"]
    login_fields = [{"name": "account", "label": "账号", "type": "text"},
                    {"name": "password", "label": "密码", "type": "password"}]
    categories = [{"value": v, "label": n} for v, n in
                  [("doujin", "同人"), ("single", "单本"), ("short", "短篇"), ("another", "其他")]]
    sorts = [{"value": v, "label": n} for v, n in
             [("new", "最新上架"), ("mv", "最多观看"), ("tf", "最多收藏"), ("mp", "最多指名")]]
    # 上游 mv_t（日榜）长期返回空数据，不展示该选项；默认落在周榜。
    leaderboard_modes = [{"value": v, "label": n} for v, n in
                         [("week", "周榜"), ("month", "月榜"), ("total", "总榜")]]

    def __init__(self, store):
        super().__init__(store)
        self.client = JmClient(store=store)

    def account_status(self):
        return {"authenticated": self.client.has_session(),
                "configured": self.client._credentials_available()}

    def login(self, values):
        self.client.login(values.get("account", ""), values.get("password", ""))

    def logout(self):
        with self.client._auth_lock:
            self.client.clear_session()
            self.store.save_account(self.id, {})
            self.client._account = self.client._password = ""

    def search_tag(self, keyword, page=1, tag_value="", tags=(), exclude_tags=()):
        if tags:
            # JM's search syntax: "+tag" must match and "-tag" excludes; bare words are not required.
            return self.client.search_tag(" ".join([f"+{tag}" for tag in tags] + [f"-{tag}" for tag in exclude_tags]), page)
        return self.client.search_tag(tag_value or keyword, page)

    def favorites(self, page=1, folder_id="0", order="mr"):
        return self.client.favorites(page, folder_id, order)

    def favorite_status(self, comic_id):
        return self.client.favorite_status(comic_id)

    def add_favorite(self, comic_id):
        return self.client.add_favorite(comic_id)

    def remove_favorite(self, comic_id):
        return self.client.remove_favorite(comic_id)

    def browse(self, action, page=1, sort="", mode="week", name=""):
        if action == "random":
            return self.client.get_recommend()
        if action == "leaderboard":
            return self.client.get_leaderboard(mode, page)
        if action == "latest":
            return self.client.get_latest(page)
        return self.client.get_category_comics(name, page, sort or "new")

    def transform_image(self, data, chapter_id, url):
        from src.services.images import descramble_jm_image
        return descramble_jm_image(data, chapter_id, url) if chapter_id else data


create_plugin = JmPlugin
