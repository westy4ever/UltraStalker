# -*- coding: utf-8 -*-
"""Optional IMDb metadata adapter.

Two modes are supported:

* A user-supplied JSON endpoint containing ``{id}``, kept for backwards
  compatibility with us27.
* The official IMDb API delivered through AWS Data Exchange.  This mode uses
  only Python's standard library and signs the Data Exchange request with
  AWS Signature Version 4, so receivers do not need boto3/botocore installed.
"""
from __future__ import absolute_import

import datetime
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request

from .netsec import credential_urlopen


class IMDbError(Exception):
    pass


def _hmac(key, value):
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


class IMDbClient:
    def __init__(
        self, endpoint="", api_key="", timeout=8,
        access_key_id="", secret_access_key="", session_token="",
        region="us-east-1", dataset_id="", revision_id="", asset_id="",
    ):
        self.endpoint = str(endpoint or "").strip()
        self.api_key = str(api_key or "").strip()
        self.timeout = max(3, min(20, int(timeout or 8)))
        self.access_key_id = str(access_key_id or "").strip()
        self.secret_access_key = str(secret_access_key or "").strip()
        self.session_token = str(session_token or "").strip()
        self.region = str(region or "us-east-1").strip() or "us-east-1"
        self.dataset_id = str(dataset_id or "").strip()
        self.revision_id = str(revision_id or "").strip()
        self.asset_id = str(asset_id or "").strip()

    @property
    def aws_configured(self):
        return bool(
            self.api_key and self.access_key_id and self.secret_access_key
            and self.dataset_id and self.revision_id and self.asset_id
        )

    @property
    def configured(self):
        return bool(self.endpoint or self.aws_configured)

    def _url(self, imdb_id):
        iid = str(imdb_id or "").strip()
        if not iid:
            return ""
        if "{id}" in self.endpoint:
            return self.endpoint.replace("{id}", urllib.parse.quote(iid))
        sep = "&" if "?" in self.endpoint else "?"
        return self.endpoint + sep + urllib.parse.urlencode({"id": iid})

    @staticmethod
    def _read_json_response(response):
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise IMDbError("IMDb response too large")
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, TypeError) as exc:
            raise IMDbError("IMDb returned invalid JSON: %s" % exc)

    def _generic_lookup(self, imdb_id):
        url = self._url(imdb_id)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() != "https":
            raise IMDbError("Generic IMDb endpoints must use HTTPS to protect API credentials")
        headers = {"User-Agent": "UltraStalker-Nova/14", "Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(url, headers=headers)
        with credential_urlopen(request, timeout=self.timeout) as response:
            return self._read_json_response(response)

    @staticmethod
    def _graphql_query(imdb_id):
        iid = json.dumps(str(imdb_id or ""))
        # Use the same credit filters documented by IMDb. Aliases keep cast,
        # directors and writers separate without relying on undocumented
        # category fields in the response.
        return (
            "{ title(id: %s) { ratingsSummary { aggregateRating voteCount } "
            "CAST: credits(first: 8, filter: { categories: [\"actor\", \"actress\", \"self\"] }) "
            "{ edges { node { name { nameText { text } } } } } "
            "DIRECTORS: credits(first: 3, filter: { categories: [\"director\"] }) "
            "{ edges { node { name { nameText { text } } } } } "
            "WRITERS: credits(first: 4, filter: { categories: [\"writer\"] }) "
            "{ edges { node { name { nameText { text } } } } } } }" % iid
        )

    def _aws_headers(self, body, now=None):
        """Build an AWS SigV4 header set for Data Exchange ``SendApiAsset``."""
        now = now or datetime.datetime.now(datetime.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        host = "api-fulfill.dataexchange.%s.amazonaws.com" % self.region
        uri = "/v1"
        payload_hash = hashlib.sha256(body).hexdigest()
        headers = {
            "content-type": "application/json",
            "host": host,
            "x-amz-date": amz_date,
            "x-amzn-dataexchange-asset-id": self.asset_id,
            "x-amzn-dataexchange-data-set-id": self.dataset_id,
            "x-amzn-dataexchange-revision-id": self.revision_id,
            "x-api-key": self.api_key,
        }
        if self.session_token:
            headers["x-amz-security-token"] = self.session_token
        signed_names = sorted(headers)
        canonical_headers = "".join("%s:%s\n" % (name, " ".join(str(headers[name]).strip().split())) for name in signed_names)
        signed_headers = ";".join(signed_names)
        canonical_request = "\n".join(("POST", uri, "", canonical_headers, signed_headers, payload_hash))
        scope = "%s/%s/dataexchange/aws4_request" % (date_stamp, self.region)
        string_to_sign = "\n".join((
            "AWS4-HMAC-SHA256", amz_date, scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ))
        date_key = _hmac(("AWS4" + self.secret_access_key).encode("utf-8"), date_stamp)
        region_key = _hmac(date_key, self.region)
        service_key = _hmac(region_key, "dataexchange")
        signing_key = _hmac(service_key, "aws4_request")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
            % (self.access_key_id, scope, signed_headers, signature)
        )
        return host, uri, headers

    def _aws_lookup(self, imdb_id):
        body = json.dumps({"query": self._graphql_query(imdb_id)}, separators=(",", ":")).encode("utf-8")
        host, uri, headers = self._aws_headers(body)
        url = "https://%s%s" % (host, uri)
        request = urllib.request.Request(url, data=body, headers={k: v for k, v in headers.items()}, method="POST")
        with credential_urlopen(request, timeout=self.timeout) as response:
            data = self._read_json_response(response)
        if isinstance(data, dict) and data.get("errors"):
            first = data.get("errors")[0] if isinstance(data.get("errors"), list) and data.get("errors") else data.get("errors")
            raise IMDbError("IMDb GraphQL error: %s" % first)
        return data

    def lookup(self, imdb_id):
        if not self.configured:
            return None
        try:
            data = self._aws_lookup(imdb_id) if self.aws_configured else self._generic_lookup(imdb_id)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(512).decode("utf-8", "replace")
            except Exception:
                pass
            raise IMDbError("IMDb HTTP %s%s" % (exc.code, (": " + detail[:180]) if detail else ""))
        except urllib.error.URLError as exc:
            raise IMDbError("IMDb connection error: %s" % getattr(exc, "reason", exc))
        except IMDbError:
            raise
        except Exception as exc:
            raise IMDbError(str(exc))
        return self._normalize(data, imdb_id)

    def _find(self, obj, keys, depth=0):
        if depth > 7:
            return None
        if isinstance(obj, dict):
            for key in keys:
                if key in obj and obj[key] not in (None, ""):
                    return obj[key]
            for value in obj.values():
                found = self._find(value, keys, depth + 1)
                if found not in (None, ""):
                    return found
        elif isinstance(obj, list):
            for value in obj[:40]:
                found = self._find(value, keys, depth + 1)
                if found not in (None, ""):
                    return found
        return None

    def _collect_credit_names(self, obj, category_ids=None, depth=0, out=None):
        if out is None:
            out = []
        if depth > 10 or len(out) >= 12:
            return out
        if isinstance(obj, dict):
            category = obj.get("category") if isinstance(obj.get("category"), dict) else {}
            category_id = str(category.get("id") or obj.get("categoryId") or "").lower()
            name_obj = obj.get("name") if isinstance(obj.get("name"), dict) else {}
            name_text = name_obj.get("nameText") if isinstance(name_obj.get("nameText"), dict) else {}
            name = name_text.get("text") or obj.get("primaryName") or obj.get("fullName")
            if name and (not category_ids or category_id in category_ids):
                clean = str(name).strip()
                if clean and clean not in out:
                    out.append(clean)
            for value in obj.values():
                self._collect_credit_names(value, category_ids, depth + 1, out)
        elif isinstance(obj, list):
            for value in obj[:50]:
                self._collect_credit_names(value, category_ids, depth + 1, out)
        return out

    def _names(self, value):
        if not value:
            return []
        if isinstance(value, str):
            return [x.strip() for x in value.split(",") if x.strip()]
        out = []
        if isinstance(value, list):
            for row in value:
                if isinstance(row, str):
                    name = row
                elif isinstance(row, dict):
                    name = row.get("name") or row.get("fullName") or row.get("primaryName")
                    if isinstance(name, dict):
                        nested = name.get("nameText") if isinstance(name.get("nameText"), dict) else {}
                        name = nested.get("text") or name.get("text")
                else:
                    name = None
                if name and str(name) not in out:
                    out.append(str(name))
        return out

    def _normalize(self, data, imdb_id):
        rating = self._find(data, ("imdbRating", "imdb_rating", "ratingValue", "aggregateRating", "rating", "score"))
        if isinstance(rating, dict):
            rating = rating.get("ratingValue") or rating.get("value") or rating.get("score")
        try:
            rating = float(str(rating).split("/")[0])
            if rating > 10 and rating <= 100:
                rating /= 10.0
            if not (0 <= rating <= 10):
                rating = None
        except Exception:
            rating = None

        cast = self._names(self._find(data, ("cast", "actors", "stars")))[:8]
        directors = self._names(self._find(data, ("directors", "director")))[:3]
        writers = self._names(self._find(data, ("writers", "writer")))[:4]
        # Official IMDb GraphQL examples use aliases when querying several
        # credit categories. Extract names directly from those aliased trees.
        if not cast:
            cast_alias = self._find(data, ("CAST",))
            cast = self._collect_credit_names(cast_alias, None)[:8]
        if not directors:
            director_alias = self._find(data, ("DIRECTORS",))
            directors = self._collect_credit_names(director_alias, None)[:3]
        if not writers:
            writer_alias = self._find(data, ("WRITERS",))
            writers = self._collect_credit_names(writer_alias, None)[:4]
        # Generic endpoint/backwards-compatible fallback for category-bearing
        # payloads that do not use the official aliases.
        if not cast:
            cast = self._collect_credit_names(data, {"actor", "actress", "self"})[:8]
        if not directors:
            directors = self._collect_credit_names(data, {"director"})[:3]
        if not writers:
            writers = self._collect_credit_names(data, {"writer", "screenwriter"})[:4]
        votes = self._find(data, ("numVotes", "voteCount", "votes"))
        return {
            "source": "IMDb", "imdb_id": str(imdb_id), "rating": rating,
            "votes": votes, "cast": cast, "directors": directors, "writers": writers,
        }
