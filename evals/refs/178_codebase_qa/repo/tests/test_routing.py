from ferrule.api.routing import Router, RouteTable, View, action, get, join


def test_join():
    assert join("/api", "v2", "/jobs/") == "/api/v2/jobs"
    assert join("", "/") == "/"


class Things(View):
    resource = "things"

    @get("")
    def index(self, request):
        return 200, {}

    @action("/{thing_id}/poke")
    def poke(self, request, thing_id):
        return 200, {"id": thing_id}


def test_view_routes_and_match():
    r = Router("/things")
    r.mount_view(Things)
    root = Router("/api/v1")
    root.include(r)
    table = RouteTable(root)
    assert sorted((x.method, x.path) for x in table.routes) == [
        ("GET", "/api/v1/things"), ("POST", "/api/v1/things/{thing_id}/poke")]
    route, params = table.match("POST", "/api/v1/things/t1/poke/")
    assert route.name == "things.poke" and params == {"thing_id": "t1"}
