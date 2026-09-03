# -*- coding: utf-8 -*-
from __future__ import absolute_import

import unittest

from Plugins.Extensions.UltraStalker import artwork_v2 as art


class _FakeTMDB(object):
    def __init__(self, mode="search"):
        self.mode=mode
        self.credential="cred"
        self.calls=[]

    def _get(self,path,params):
        self.calls.append((path,dict(params or {})))
        if path.startswith("/find/"):
            return {"movie_results":[{
                "id":222,
                "title":"The Witch: Part 2. The Other One",
                "original_title":"마녀 Part2. The Other One",
                "release_date":"2022-06-15",
            }]}
        if path=="/search/movie":
            q=str((params or {}).get("query") or "")
            if "Witch" in q:
                return {"results":[{
                    "id":222,
                    "title":"The Witch: Part 2. The Other One",
                    "original_title":"마녀 Part2. The Other One",
                    "release_date":"2022-06-15",
                }]}
            return {"results":[]}
        if path=="/movie/222":
            return {
                "id":222,
                "title":"The Witch: Part 2. The Other One",
                "original_title":"마녀 Part2. The Other One",
                "release_date":"2022-06-15",
                "images":{"backdrops":[{"file_path":"/witch.jpg","width":1920,"height":1080,"iso_639_1":None}]},
            }
        if path=="/movie/222/images":
            return {"backdrops":[{"file_path":"/witch.jpg","width":1920,"height":1080,"iso_639_1":None}]}
        return {}


class BackdropOnlyRescueTests(unittest.TestCase):
    def setUp(self):
        self.saved={
            "hdd_ready":art.hdd_ready,
            "_load_canonical":art._load_canonical,
            "_save_canonical":art._save_canonical,
            "_valid_backdrop":art._valid_backdrop,
            "_resolve_backdrop_v4":art._resolve_backdrop_v4,
            "_mark_backdrop_state":art._mark_backdrop_state,
            "_schedule_backdrop_recovery":art._schedule_backdrop_recovery,
            "_canonical_paths":art._canonical_paths,
        }
        art.hdd_ready=lambda:True
        art._load_canonical=lambda mt,tid:{}
        art._save_canonical=lambda mt,tid,data:True
        art._valid_backdrop=lambda path:bool(path)
        art._mark_backdrop_state=lambda *a,**k:True
        art._schedule_backdrop_recovery=lambda *a,**k:True
        art._canonical_paths=lambda mt,tid:{"backdrop":"/tmp/backdrop.jpg"}
        art._resolve_backdrop_v4=lambda client,language,mt,tid,details,images,poster,target,cancel:(
            "/tmp/backdrop.jpg","/witch.jpg","https://image.tmdb.org/t/p/w1280/witch.jpg","gallery:w1280"
        )

    def tearDown(self):
        for key,value in self.saved.items():
            setattr(art,key,value)

    def _resolver(self):
        obj=art.ArtworkV2.__new__(art.ArtworkV2)
        obj.language="ar-EG"
        obj.lang="ar"
        obj.timeout=5
        obj.tmdb=_FakeTMDB()
        return obj

    def test_strict_title_year_rescues_backdrop(self):
        item={
            "name":"The Witch: Part 2. The Other One (2022)",
            "_raw_name":"The Witch: Part 2. The Other One (2022)",
            "year":"2022",
        }
        out=self._resolver().resolve_backdrop_only("p","vod",item)
        self.assertEqual(out.get("tmdb_id"),222)
        self.assertEqual(out.get("backdrop_local"),"/tmp/backdrop.jpg")
        self.assertTrue(str(out.get("identity_source") or "").startswith("backdrop_only:"))

    def test_imdb_find_is_used_as_exact_rescue(self):
        item={
            "name":"The Witch: Part 2. The Other One (2022)",
            "_raw_name":"The Witch: Part 2. The Other One (2022)",
            "imdb_id":"tt13721828",
            "year":"2022",
        }
        resolver=self._resolver()
        out=resolver.resolve_backdrop_only("p","vod",item)
        self.assertEqual(out.get("tmdb_id"),222)
        self.assertTrue(any(path.startswith("/find/tt13721828") for path,_ in resolver.tmdb.calls))

    def test_unrelated_title_does_not_accept_witch_candidate(self):
        item={"name":"Completely Different Film (2022)","year":"2022"}
        out=self._resolver().resolve_backdrop_only("p","vod",item)
        self.assertEqual(out,{})

    def test_backdrop_resolver_never_receives_a_poster_source(self):
        seen=[]
        def fake(client,language,mt,tid,details,images,poster,target,cancel):
            seen.append(poster)
            return "/tmp/backdrop.jpg","/witch.jpg","url","gallery"
        art._resolve_backdrop_v4=fake
        item={"name":"The Witch: Part 2. The Other One (2022)","year":"2022"}
        out=self._resolver().resolve_backdrop_only("p","vod",item)
        self.assertEqual(out.get("backdrop_local"),"/tmp/backdrop.jpg")
        self.assertEqual(seen,[None])


if __name__=="__main__":
    unittest.main()
