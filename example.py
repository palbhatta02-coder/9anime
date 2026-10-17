"""
Example script demonstrating how to import and use AniListScraper in your own Python code.
"""

from anilist_scraper import AniListScraper


def main():
    scraper = AniListScraper()

    # 1. Search anime
    print("--- 1. Search Anime ---")
    results = scraper.search(query="Steins;Gate", media_type="ANIME", per_page=3)
    for anime in results:
        title = anime["title"]["english"] or anime["title"]["romaji"]
        print(f"[{anime['id']}] {title} (Score: {anime['averageScore']}%, Format: {anime['format']})")

    # 2. Get Top 5 Anime
    print("\n--- 2. Top 5 Anime ---")
    top_anime = scraper.get_top(media_type="ANIME", limit=5)
    for rank, anime in enumerate(top_anime, 1):
        title = anime["title"]["english"] or anime["title"]["romaji"]
        print(f"#{rank} {title} - Score: {anime['averageScore']}% (Episodes: {anime['episodes']})")

    # 3. Get Media by ID
    print("\n--- 3. Single Anime Details by ID ---")
    item = scraper.get_by_id(16498)  # Attack on Titan
    if item:
        print(f"Title: {item['title']['english']} ({item['title']['romaji']})")
        print(f"Genres: {', '.join(item['genres'])}")
        print(f"Studios: {', '.join([s['name'] for s in item['studios']['nodes']])}")
        print(f"Site URL: {item['siteUrl']}")

    # 4. Exporting programmatic results
    print("\n--- 4. Exporting to JSON and CSV ---")
    scraper.export_json(top_anime, "top_5_anime.json")
    scraper.export_csv(top_anime, "top_5_anime.csv")


if __name__ == "__main__":
    main()
