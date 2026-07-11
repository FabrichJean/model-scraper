import scrapy


class ModelProfileItem(scrapy.Item):
    # Identification
    url = scrapy.Field()
    model_type = scrapy.Field()  # 'model', 'pornstar', 'channel', etc.

    # Profil
    name = scrapy.Field()
    username = scrapy.Field()
    bio = scrapy.Field()
    avatar_url = scrapy.Field()
    cover_url = scrapy.Field()

    # Stats
    views = scrapy.Field()
    subscribers = scrapy.Field()
    videos_count = scrapy.Field()
    photos_count = scrapy.Field()
    rank = scrapy.Field()

    # Infos supplémentaires
    country = scrapy.Field()
    gender = scrapy.Field()
    age = scrapy.Field()
    tags = scrapy.Field()  # list

    # Vidéos (depuis /shorties/userProfile POST)
    videos = scrapy.Field()              # list: [{title, imageUrl, link, duration, vkey}]
    videos_page_url = scrapy.Field()     # URL shorties/{hash}#openProfile
    userprofile_api = scrapy.Field()     # réponse JSON brute
    subscribers_label = scrapy.Field()   # ex: "78.9K Subscribers"
    videos_count_label = scrapy.Field()  # ex: "211 Videos"

    # Metadata
    scraped_at = scrapy.Field()
    raw_stats = scrapy.Field()  # dict brut pour debug
