import io
import json
import pytest

from tools.refresh_approved_provider_feed import execute

class Response:
    def __init__(self, raw):
        self.raw=raw
    def __enter__(self):
        return self
    def __exit__(self,*_):
        return False
    def read(self, max_bytes):
        return self.raw

def _provider(public=True):
    return {
        "source": {"name": "Illustrative Vendor",
                   "permission_reference": "test fixture permission only",
                   "public_redistribution_allowed": public},
        "patch": "16.20", "rank":"Emerald+","region":"EUW",
        "queue":"RANKED_SOLO_5x5",
        "champions": [{"role":"Mid","champion":"Veigar","win_rate":52.0,"games":4000}],
        "matchups": [{"role":"Mid","enemy":"Ahri","response":"Veigar",
                      "response_advantage":3.1,"games":1200}],
    }

def test_provider_refresh_requires_actual_public_redistribution(monkeypatch,tmp_path):
    body=json.dumps(_provider(public=False)).encode()
    monkeypatch.setattr("tools.refresh_approved_provider_feed.urllib.request.urlopen",
                        lambda req,timeout: Response(body))
    output=tmp_path/"licensed.json"
    with pytest.raises(ValueError,match="redistribution"):
        execute("https://example.test/feed.json",output=output)
    assert not output.exists()

def test_provider_refresh_is_idempotent_and_attributed(monkeypatch,tmp_path):
    body=json.dumps(_provider()).encode()
    monkeypatch.setattr("tools.refresh_approved_provider_feed.urllib.request.urlopen",
                        lambda req,timeout: Response(body))
    path=tmp_path/"aggregate.json"
    first=execute("https://example.test/feed.json",output=path)
    assert first["updated"] and first["matchups"]==1
    assert execute("https://example.test/feed.json",output=path)["updated"] is False
    stored=json.loads(path.read_text())
    assert stored["sources"][0]["name"]=="Illustrative Vendor"

def test_provider_fetch_disallows_non_https(monkeypatch,tmp_path):
    with pytest.raises(ValueError,match="HTTPS"):
        execute("http://example.test/feed",output=tmp_path/"x.json")
