"""
AniList Scraper & Extractor
A robust tool to scrape/extract anime, manga, and user list data from AniList via their GraphQL API.
Includes automatic rate limit handling, pagination, and JSON/CSV export support.
"""

import sys
import time
import json
import csv
import argparse
from typing import Dict, Any, List, Optional
import requests

# Ensure clean UTF-8 output on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ANILIST_API_URL = "https://graphql.anilist.co"

MEDIA_FIELDS = """
id
idMal
title {
    romaji
    english
    native
}
type
format
status
description(asHtml: false)
season
seasonYear
episodes
duration
chapters
volumes
genres
averageScore
meanScore
popularity
favourites
studios(isMain: true) {
    nodes {
        name
    }
}
coverImage {
    large
    extraLarge
}
bannerImage
siteUrl
startDate {
    year
    month
    day
}
endDate {
    year
    month
    day
}
"""

SEARCH_MEDIA_QUERY = f"""
query ($search: String, $type: MediaType, $sort: [MediaSort], $page: Int, $perPage: Int) {{
    Page(page: $page, perPage: $perPage) {{
        pageInfo {{
            total
            currentPage
            lastPage
            hasNextPage
            perPage
        }}
        media(search: $search, type: $type, sort: $sort) {{
            {MEDIA_FIELDS}
        }}
    }}
}}
"""

MEDIA_BY_ID_QUERY = f"""
query ($id: Int) {{
    Media(id: $id) {{
        {MEDIA_FIELDS}
    }}
}}
"""

USER_LIST_QUERY = """
query ($userName: String, $type: MediaType) {
    MediaListCollection(userName: $userName, type: $type) {
        lists {
            name
            isCustomList
            status
            entries {
                id
                status
                score
                progress
                repeat
                updatedAt
                media {
                    id
                    title {
                        romaji
                        english
                    }
                    type
                    format
                    status
                    episodes
                    averageScore
                    genres
                    siteUrl
                }
            }
        }
    }
}
"""


class AniListScraper:
    def __init__(self, timeout: int = 15, max_retries: int = 3):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AniListScraper/1.0 (Python)"
        })
        self.timeout = timeout
        self.max_retries = max_retries

    def _execute_query(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a GraphQL query with rate limit detection and retries."""
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.post(
                    ANILIST_API_URL,
                    json={"query": query, "variables": variables},
                    timeout=self.timeout
                )

                # Rate Limit Handling (HTTP 429)
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    print(f"[!] Rate limited by AniList. Waiting {retry_after} seconds...", file=sys.stderr)
                    time.sleep(retry_after)
                    continue

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")

                data = response.json()
                if "errors" in data:
                    error_msg = "; ".join([e.get("message", "Unknown error") for e in data["errors"]])
                    raise RuntimeError(f"AniList GraphQL error: {error_msg}")

                return data.get("data", {})

            except (requests.RequestException, RuntimeError) as exc:
                if attempt == self.max_retries:
                    raise
                print(f"[!] Request failed (attempt {attempt}/{self.max_retries}): {exc}. Retrying in {attempt * 2}s...", file=sys.stderr)
                time.sleep(attempt * 2)

        return {}

    def search(self, query: str, media_type: str = "ANIME", page: int = 1, per_page: int = 10) -> List[Dict[str, Any]]:
        """Search anime or manga by title."""
        variables = {
            "search": query,
            "type": media_type.upper(),
            "page": page,
            "perPage": min(per_page, 50)
        }
        res = self._execute_query(SEARCH_MEDIA_QUERY, variables)
        return res.get("Page", {}).get("media", [])

    def get_top(self, media_type: str = "ANIME", limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch highest-rated anime or manga."""
        return self._fetch_sorted(media_type=media_type, sort=["SCORE_DESC"], limit=limit)

    def get_trending(self, media_type: str = "ANIME", limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch trending anime or manga."""
        return self._fetch_sorted(media_type=media_type, sort=["TRENDING_DESC"], limit=limit)

    def get_popular(self, media_type: str = "ANIME", limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch most popular anime or manga."""
        return self._fetch_sorted(media_type=media_type, sort=["POPULARITY_DESC"], limit=limit)

    def get_by_id(self, media_id: int) -> Optional[Dict[str, Any]]:
        """Fetch single anime or manga by AniList ID."""
        res = self._execute_query(MEDIA_BY_ID_QUERY, {"id": media_id})
        return res.get("Media")

    def get_user_list(self, username: str, media_type: str = "ANIME") -> List[Dict[str, Any]]:
        """Fetch all anime/manga lists for a given AniList username."""
        variables = {
            "userName": username,
            "type": media_type.upper()
        }
        res = self._execute_query(USER_LIST_QUERY, variables)
        collection = res.get("MediaListCollection", {})
        all_entries = []
        for lst in collection.get("lists", []):
            list_name = lst.get("name")
            for entry in lst.get("entries", []):
                flattened = {
                    "list_name": list_name,
                    "entry_id": entry.get("id"),
                    "user_status": entry.get("status"),
                    "user_score": entry.get("score"),
                    "user_progress": entry.get("progress"),
                    "user_repeat": entry.get("repeat"),
                    "media_id": entry.get("media", {}).get("id"),
                    "title_romaji": entry.get("media", {}).get("title", {}).get("romaji"),
                    "title_english": entry.get("media", {}).get("title", {}).get("english"),
                    "format": entry.get("media", {}).get("format"),
                    "media_status": entry.get("media", {}).get("status"),
                    "episodes": entry.get("media", {}).get("episodes"),
                    "average_score": entry.get("media", {}).get("averageScore"),
                    "genres": ", ".join(entry.get("media", {}).get("genres", [])),
                    "url": entry.get("media", {}).get("siteUrl")
                }
                all_entries.append(flattened)
        return all_entries

    def _fetch_sorted(self, media_type: str, sort: List[str], limit: int) -> List[Dict[str, Any]]:
        results = []
        page = 1
        per_page = 50

        while len(results) < limit:
            to_fetch = min(per_page, limit - len(results))
            variables = {
                "type": media_type.upper(),
                "sort": sort,
                "page": page,
                "perPage": to_fetch
            }
            res = self._execute_query(SEARCH_MEDIA_QUERY, variables)
            page_data = res.get("Page", {})
            media_items = page_data.get("media", [])
            if not media_items:
                break

            results.extend(media_items)
            page_info = page_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break

            page += 1
            time.sleep(0.5)  # Friendly pacing to respect rate limits

        return results[:limit]

    @staticmethod
    def flatten_media(item: Dict[str, Any]) -> Dict[str, Any]:
        """Flattens nested GraphQL media response into a single-level dictionary for CSV export."""
        title = item.get("title") or {}
        studios = [s.get("name") for s in item.get("studios", {}).get("nodes", []) if s.get("name")]
        genres = item.get("genres") or []
        start_date = item.get("startDate") or {}
        end_date = item.get("endDate") or {}

        def format_date(d: Dict[str, Any]) -> str:
            y, m, day = d.get("year"), d.get("month"), d.get("day")
            if y and m and day:
                return f"{y:04d}-{m:02d}-{day:02d}"
            elif y:
                return str(y)
            return ""

        return {
            "id": item.get("id"),
            "mal_id": item.get("idMal"),
            "title_english": title.get("english") or "",
            "title_romaji": title.get("romaji") or "",
            "title_native": title.get("native") or "",
            "type": item.get("type"),
            "format": item.get("format"),
            "status": item.get("status"),
            "episodes": item.get("episodes"),
            "duration_minutes": item.get("duration"),
            "chapters": item.get("chapters"),
            "volumes": item.get("volumes"),
            "season": item.get("season"),
            "season_year": item.get("seasonYear"),
            "average_score": item.get("averageScore"),
            "mean_score": item.get("meanScore"),
            "popularity": item.get("popularity"),
            "favourites": item.get("favourites"),
            "studios": ", ".join(studios),
            "genres": ", ".join(genres),
            "start_date": format_date(start_date),
            "end_date": format_date(end_date),
            "cover_image": (item.get("coverImage") or {}).get("extraLarge") or (item.get("coverImage") or {}).get("large") or "",
            "site_url": item.get("siteUrl") or "",
            "description": (item.get("description") or "").replace("\n", " ").strip()
        }

    @classmethod
    def export_json(cls, data: Any, filepath: str):
        """Save data to JSON file with indentation."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[+] Successfully exported JSON: {filepath}")

    @classmethod
    def export_csv(cls, data: List[Dict[str, Any]], filepath: str):
        """Save flat or media items to CSV file."""
        if not data:
            print("[!] No records to export to CSV.", file=sys.stderr)
            return

        # Flatten if it's raw media
        if "title" in data[0] and isinstance(data[0]["title"], dict):
            flat_data = [cls.flatten_media(m) for m in data]
        else:
            flat_data = data

        fieldnames = list(flat_data[0].keys())
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(flat_data)
        print(f"[+] Successfully exported CSV: {filepath}")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="AniList Scraper & Data Extractor: Fetch anime, manga, and user lists directly to JSON or CSV."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-s", "--search", type=str, help="Search query (e.g., 'Frieren', 'Jujutsu Kaisen')")
    group.add_argument("--top", type=int, nargs="?", const=20, help="Scrape top ranked items (default: 20)")
    group.add_argument("--trending", type=int, nargs="?", const=20, help="Scrape trending items (default: 20)")
    group.add_argument("--popular", type=int, nargs="?", const=20, help="Scrape most popular items (default: 20)")
    group.add_argument("--id", type=int, help="Fetch a single media item by its AniList ID")
    group.add_argument("-u", "--user", type=str, help="Fetch anime/manga lists for a given AniList username")

    parser.add_argument(
        "-t", "--type",
        choices=["ANIME", "MANGA"],
        default="ANIME",
        help="Media type: ANIME (default) or MANGA"
    )
    parser.add_argument(
        "-f", "--format",
        choices=["json", "csv", "both"],
        default="json",
        help="Export format: json (default), csv, or both"
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Base filename for exported files (without extension, defaults to auto-generated name)"
    )
    return parser.parse_args()


def main():
    args = parse_arguments()
    scraper = AniListScraper()

    print("[*] Contacting AniList GraphQL API...")
    data = None
    output_base = args.output

    if args.search:
        print(f"[*] Searching {args.type} for '{args.search}'...")
        data = scraper.search(args.search, media_type=args.type, per_page=20)
        if not output_base:
            clean_search = "".join(c if c.isalnum() else "_" for c in args.search).strip("_")
            output_base = f"search_{args.type.lower()}_{clean_search}"

    elif args.top is not None:
        count = args.top if isinstance(args.top, int) else 20
        print(f"[*] Fetching top {count} {args.type}...")
        data = scraper.get_top(media_type=args.type, limit=count)
        if not output_base:
            output_base = f"top_{args.type.lower()}_{count}"

    elif args.trending is not None:
        count = args.trending if isinstance(args.trending, int) else 20
        print(f"[*] Fetching trending {count} {args.type}...")
        data = scraper.get_trending(media_type=args.type, limit=count)
        if not output_base:
            output_base = f"trending_{args.type.lower()}_{count}"

    elif args.popular is not None:
        count = args.popular if isinstance(args.popular, int) else 20
        print(f"[*] Fetching most popular {count} {args.type}...")
        data = scraper.get_popular(media_type=args.type, limit=count)
        if not output_base:
            output_base = f"popular_{args.type.lower()}_{count}"

    elif args.id:
        print(f"[*] Fetching details for ID {args.id}...")
        item = scraper.get_by_id(args.id)
        data = [item] if item else []
        if not output_base:
            output_base = f"anilist_{args.id}"

    elif args.user:
        print(f"[*] Fetching {args.type} list for user '{args.user}'...")
        data = scraper.get_user_list(args.user, media_type=args.type)
        if not output_base:
            output_base = f"user_{args.user}_{args.type.lower()}_list"

    if not data:
        print("[!] No records found matching your request.")
        return

    print(f"[+] Retrieved {len(data)} records.")

    # Export
    if args.format in ("json", "both"):
        json_path = f"{output_base}.json"
        scraper.export_json(data, json_path)

    if args.format in ("csv", "both"):
        csv_path = f"{output_base}.csv"
        scraper.export_csv(data, csv_path)


if __name__ == "__main__":
    main()
