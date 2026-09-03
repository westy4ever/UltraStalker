# -*- coding: utf-8 -*-
from __future__ import absolute_import
import unittest
from Plugins.Extensions.UltraStalker.artwork_v2 import ArtworkV2

class _FakeTMDB(object):
    def __init__(self, provider_candidate, search_candidate):
        self.provider_candidate=dict(provider_candidate)
        self.search_candidate=dict(search_candidate)
    def _get(self,path,params):
        if path.startswith("/movie/"):
            return dict(self.provider_candidate)
        if path=="/search/movie":
            return {"results":[dict(self.search_candidate)]}
        if path=="/search/tv":
            return {"results":[]}
        if path=="/search/multi":
            row=dict(self.search_candidate);row["media_type"]="movie"
            return {"results":[row]}
        return {}

class ProviderTMDBIdentityTests(unittest.TestCase):
    def _resolver(self,provider_candidate,search_candidate):
        obj=ArtworkV2.__new__(ArtworkV2)
        obj.language="ar-EG";obj.lang="ar";obj.timeout=4
        obj.tmdb=_FakeTMDB(provider_candidate,search_candidate)
        return obj

    def test_wrong_provider_tmdb_id_named_pure_is_rejected(self):
        resolver=self._resolver(
            {"id":111,"title":"Pure","original_title":"Pure","release_date":"2025-01-01","overview":""},
            {"id":222,"title":"درويش","original_title":"درويش","release_date":"2025-08-01","overview":""},
        )
        item={"name":"درويش Pure (2025)","_raw_name":"درويش Pure (2025)","_provider_name":"Pure","tmdb_id":"111","year":"2025"}
        mt,tid,confidence,source,seed=resolver._resolve_identity("vod",item,{})
        self.assertEqual(mt,"movie")
        self.assertEqual(tid,222)
        self.assertNotEqual(source,"provider_tmdb_verified")

    def test_matching_provider_tmdb_id_is_kept(self):
        resolver=self._resolver(
            {"id":333,"title":"درويش","original_title":"درويش","release_date":"2025-08-01","overview":""},
            {"id":444,"title":"درويش","original_title":"درويش","release_date":"2025-08-01","overview":""},
        )
        item={"name":"درويش Pure (2025)","_raw_name":"درويش Pure (2025)","tmdb_id":"333","year":"2025"}
        mt,tid,confidence,source,seed=resolver._resolve_identity("vod",item,{})
        self.assertEqual(tid,333)
        self.assertEqual(source,"provider_tmdb_verified")

if __name__=="__main__":
    unittest.main()
