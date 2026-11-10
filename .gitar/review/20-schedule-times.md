# Schedule and timezone rules

- Talk and message timestamps must use ISO-8601 date-time strings consistently.
- Do not mix naive local strings and UTC without making the convention clear in code/docs.
- Afternoon planner windows and “after lunch / next hour” filters must compare against the same timezone-aware or consistently naive ISO fields used at ingest.
- When changing sample `data/talks.json` times, keep `start` before `end` and avoid overlapping slots in a single proposed stick-together itinerary unless conflict skipping is intentional.
