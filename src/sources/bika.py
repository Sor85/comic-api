from src.clients.bika import BikaClient
from src.sources import SourcePlugin


class BikaPlugin(SourcePlugin):
    id = "bika"
    name = "哔咔漫画"
    capabilities = ["search", "tag_search", "author_search", "search_categories", "detail", "pages", "latest", "category", "leaderboard", "random", "login", "favorites", "add_favorite", "remove_favorite"]
    login_fields = [{"name": "account", "label": "账号", "type": "text"},
                    {"name": "password", "label": "密码", "type": "password"}]
    categories = [{"value": name, "label": name} for name in
                  ["嗶咔漢化", "同人", "全彩", "少女漫畫", "耽美", "妹子", "治癒", "都市", "冒險"]]
    sorts = [{"value": v, "label": n} for v, n in
             [("dd", "最新上架"), ("ld", "最多喜欢"), ("vd", "最多观看"), ("da", "最旧上架")]]
    search_sorts = sorts
    tag_sorts = sorts
    latest_sorts = sorts
    leaderboard_modes = [{"value": v, "label": n} for v, n in [("day", "日榜"), ("week", "周榜"), ("month", "月榜")]]

    def __init__(self, store):
        super().__init__(store)
        self.client = BikaClient(store=store)

    def account_status(self):
        return {"authenticated": bool(self.client.authorization), "configured": self.client._credentials_available()}

    def login(self, values):
        self.client.login(values.get("account", ""), values.get("password", ""))

    def logout(self):
        with self.client._auth_lock:
            self.store.save_account(self.id, {})
            self.client.authorization = self.client._account = self.client._password = ""
            self.client._favorite_pages.clear()

    def favorites(self, page=1, sort="dd"):
        return self.client.favorites(page, sort)

    def favorite_status(self, comic_id):
        return self.client.favorite_status(comic_id)

    def add_favorite(self, comic_id):
        return self.client.add_favorite(comic_id)

    def remove_favorite(self, comic_id):
        return self.client.remove_favorite(comic_id)

    def search(self, keyword, page=1, sort="", category=""):
        return self.client.search(keyword, page, sort or "dd", category=category)

    def search_tag(self, keyword, page=1, sort="", tag_value="", category=""):
        return self.client.search_tag(tag_value or keyword, page, sort or "dd", category)

    def search_author(self, keyword, page=1, sort="", category=""):
        return self.client.search_author(keyword, page, sort or "dd", category)

    def search_categories(self):
        return self.client.search_categories()

    def browse(self, action, page=1, sort="", mode="day", name=""):
        self.client.ensure_authenticated()
        if action == "random":
            return self.client.get_random()
        if action == "leaderboard":
            return self.client.get_leaderboard(mode)[(page - 1) * 20:page * 20]
        if action == "latest":
            return self.client.get_latest(page, sort or "dd")
        return self.client.get_category_comics(name, page, sort or "dd")


create_plugin = BikaPlugin
