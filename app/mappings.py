"""Index mappings. semantic_text uses Elastic Inference Service (Jina on Cloud)."""

TALKS_MAPPING = {
    "mappings": {
        "properties": {
            "talk_id": {"type": "keyword"},
            "title": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
            },
            "abstract": {"type": "semantic_text"},
            "abstract_text": {"type": "text"},
            "track": {"type": "keyword"},
            "speakers": {"type": "keyword"},
            "tags": {"type": "keyword"},
            "room": {"type": "keyword"},
            "location": {"type": "geo_point"},
            "start": {"type": "date"},
            "end": {"type": "date"},
            "day": {"type": "keyword"},
            "level": {"type": "keyword"},
        }
    }
}

MESSAGES_MAPPING = {
    "mappings": {
        "properties": {
            "message_id": {"type": "keyword"},
            "group_id": {"type": "keyword"},
            "sender": {"type": "keyword"},
            "body": {"type": "semantic_text"},
            "body_text": {"type": "text"},
            "role": {"type": "keyword"},
            "timestamp": {"type": "date"},
            "source": {"type": "keyword"},
        }
    }
}

ITINERARIES_MAPPING = {
    "mappings": {
        "properties": {
            "itinerary_id": {"type": "keyword"},
            "group_id": {"type": "keyword"},
            "mode": {"type": "keyword"},
            "summary": {"type": "text"},
            "talk_ids": {"type": "keyword"},
            "created_at": {"type": "date"},
            "interests": {"type": "keyword"},
            "conflicts_resolved": {"type": "integer"},
            "reconvene_room": {"type": "keyword"},
        }
    }
}

QUERY_LOGS_MAPPING = {
    "mappings": {
        "properties": {
            "query_id": {"type": "keyword"},
            "group_id": {"type": "keyword"},
            "query": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword", "ignore_above": 512}},
            },
            "query_text": {"type": "text"},
            "mode": {"type": "keyword"},
            "kind": {"type": "keyword"},
            "result_ids": {"type": "keyword"},
            "filters": {"type": "object", "enabled": True},
            "explanation": {"type": "object", "enabled": True},
            "timestamp": {"type": "date"},
        }
    }
}

FEEDBACK_MAPPING = {
    "mappings": {
        "properties": {
            "feedback_id": {"type": "keyword"},
            "query_id": {"type": "keyword"},
            "group_id": {"type": "keyword"},
            "rating": {"type": "keyword"},
            "talk_id": {"type": "keyword"},
            "note": {"type": "text"},
            "timestamp": {"type": "date"},
        }
    }
}
