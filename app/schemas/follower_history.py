from datetime import datetime, timezone

# inside your sync function
today = datetime.now(timezone.utc).date()

for playlist in playlists:
    try:
        # get latest spotify followers
        spotify_followers = playlist_data["followers"]["total"]

        # update playlist followers
        supabase.table("playlists").update({
            "followers": spotify_followers
        }).eq("id", playlist["id"]).execute()

        # insert today's follower history only
        # if today's row already exists, update it instead of creating duplicates
        existing = supabase.table("follower_history").select("id").eq(
            "playlist_id", playlist["id"]
        ).eq(
            "date", str(today)
        ).execute()

        if existing.data:
            response = supabase.table("follower_history").update({
                "followers": spotify_followers
            }).eq(
                "id", existing.data[0]["id"]
            ).execute()

            print("Updated today's follower history:", response.data)

        else:
            response = supabase.table("follower_history").insert({
                "playlist_id": playlist["id"],
                "followers": spotify_followers,
                "date": str(today)
            }).execute()

            print("Inserted today's follower history:", response.data)

    except Exception as e:
        print("Sync failed for playlist:", playlist.get("id"))
        print("Error:", str(e))