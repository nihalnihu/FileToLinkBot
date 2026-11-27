# (c) @biisal
#(c) Adarsh-Goel
import datetime
import re
from typing import Optional
from bson.binary import Binary
import motor.motor_asyncio


class Database:
    def __init__(self, uri, database_name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.col = self.db.users
        self.bannedList = self.db.bannedList
        self.videos = self.db.videos
        self.thumbnails = self.db.thumbnails

    def new_user(self, id, name: str = None, username: str = None):
        doc = dict(
            id=int(id),
            join_date=datetime.date.today().isoformat()
        )
        if name:
            doc["name"] = str(name).strip()
        if username:
            doc["username"] = str(username).strip().lstrip("@")
        return doc

    async def add_user(self, id, name: str = None, username: str = None):
        user = self.new_user(id, name=name, username=username)
        await self.col.update_one(
            {"id": int(id)},
            {"$setOnInsert": {"join_date": user["join_date"]}, "$set": {k: v for k, v in user.items() if k != "join_date"}},
            upsert=True,
        )

    async def update_user_profile(self, id, name: str = None, username: str = None):
        updates = {}
        if name is not None and str(name).strip():
            updates["name"] = str(name).strip()
        if username is not None:
            updates["username"] = str(username).strip().lstrip("@")
        if updates:
            await self.col.update_one({"id": int(id)}, {"$set": updates})
        
    async def add_user_pass(self, id, ag_pass):
        await self.add_user(int(id))
        await self.col.update_one({'id': int(id)}, {'$set': {'ag_p': ag_pass}})
    
    async def get_user_pass(self, id):
        user_pass = await self.col.find_one({'id': int(id)})
        return user_pass.get("ag_p", None) if user_pass else None
    
    async def is_user_exist(self, id):
        user = await self.col.find_one({'id': int(id)})
        return True if user else False

    async def total_users_count(self):
        count = await self.col.count_documents({})
        return count

    async def get_all_users(self):
        all_users = self.col.find({})
        return all_users

    async def delete_user(self, user_id):
        await self.col.delete_many({'id': int(user_id)})
        
    async def ban_user(self , user_id):
        user = await self.bannedList.find_one({'banId' : int(user_id)})
        if user:
            return False
        else:
            await self.bannedList.insert_one({'banId' : int(user_id)})
            return True
        
    async def is_banned(self , user_id):
        user = await self.bannedList.find_one({'banId' : int(user_id)})
        return True if user else False
    
    async def is_unbanned(self , user_id):
        try : 
            if await self.bannedList.find_one({'banId' : int(user_id)}):
                await self.bannedList.delete_one({'banId' : int(user_id)})
                return True
            else:
                return False
        except Exception as e:
            e = f'Fᴀɪʟᴇᴅ ᴛᴏ ᴜɴʙᴀɴ.Rᴇᴀsᴏɴ : {e}'
            print(e)
            return e

    async def add_video(self, video_doc: dict):
        msg_id = int(video_doc["msg_id"])
        await self.videos.update_one(
            {"msg_id": msg_id},
            {"$set": video_doc},
            upsert=True,
        )

    async def get_videos(
        self,
        query_text: str = None,
        tmdb_id: int = None,
        limit: int = 20,
        skip: int = 0,
        include_hidden: bool = False,
    ):
        query = {}
        if not include_hidden:
            query["hidden_for_users"] = {"$ne": True}
        if tmdb_id is not None:
            query["tmdb_id"] = int(tmdb_id)
        if query_text and query_text.strip():
            safe_q = re.escape(query_text.strip())
            pattern = {"$regex": safe_q, "$options": "i"}
            query["$or"] = [
                {"title": pattern},
                {"file_name": pattern},
                {"stream_code": pattern},
                {"tmdb_title": pattern},
                {"language": pattern},
                {"quality": pattern},
            ]
        max_items = max(1, min(int(limit), 100))
        offset = max(0, int(skip))
        cursor = (
            self.videos.find(query, {"_id": 0})
            .sort("created_at", -1)
            .skip(offset)
            .limit(max_items)
        )
        return await cursor.to_list(length=max_items)

    async def get_video_by_code(self, code: str):
        raw = (code or "").strip()
        if not raw:
            return None
        normalized = raw.upper()
        doc = await self.videos.find_one({"stream_code": normalized}, {"_id": 0})
        if doc:
            return doc
        # Fallback if user pasted message id or partial code without MRDB- prefix
        m = re.match(r"^(?:MRDB-)?(\d+)-([A-Z0-9_-]{6})$", normalized)
        if m:
            doc = await self.videos.find_one(
                {"msg_id": int(m.group(1)), "hash": {"$regex": f"^{re.escape(m.group(2))}$", "$options": "i"}},
                {"_id": 0},
            )
            if doc:
                return doc
        return await self.videos.find_one(
            {"stream_code": {"$regex": f"^{re.escape(raw)}$", "$options": "i"}},
            {"_id": 0},
        )

    async def get_video_duration(self, msg_id: int) -> int:
        doc = await self.videos.find_one(
            {"msg_id": int(msg_id)}, {"_id": 0, "duration": 1}
        )
        if doc and doc.get("duration"):
            try:
                return max(0, int(doc["duration"]))
            except (TypeError, ValueError):
                return 0
        return 0

    async def save_video_duration(self, msg_id: int, duration_seconds: int):
        dur = max(0, int(duration_seconds or 0))
        if dur <= 0:
            return
        await self.videos.update_one(
            {"msg_id": int(msg_id)},
            {"$set": {"duration": dur}},
        )

    async def get_video_by_identifier(self, identifier: str):
        raw = (identifier or "").strip()
        if not raw:
            return None
        if raw.isdigit():
            doc = await self.videos.find_one({"msg_id": int(raw)}, {"_id": 0})
            if doc:
                return doc
        return await self.get_video_by_code(raw)

    async def save_audio_tracks(self, msg_id: int, tracks: list):
        if not isinstance(tracks, list):
            return
        await self.videos.update_one(
            {"msg_id": int(msg_id)},
            {"$set": {"audio_tracks": tracks}},
        )

    async def update_video_metadata(self, identifier: str, updates: dict):
        doc = await self.get_video_by_identifier(identifier)
        if not doc or doc.get("msg_id") is None:
            return None

        msg_id = int(doc["msg_id"])
        set_fields = {}
        unset_fields = {}

        if "title" in updates:
            val = str(updates["title"] or "").strip()
            if val:
                set_fields["title"] = val
                set_fields["metadata_edited"] = True

        for str_key in ("quality", "language", "overview"):
            if str_key in updates:
                val = str(updates[str_key] or "").strip()
                if val:
                    set_fields[str_key] = val
                else:
                    unset_fields[str_key] = ""

        if "year" in updates:
            raw_y = updates["year"]
            if raw_y is None or str(raw_y).strip() == "":
                unset_fields["year"] = ""
            else:
                try:
                    yr = int(raw_y)
                    if 1900 <= yr <= 2100:
                        set_fields["year"] = yr
                except (TypeError, ValueError):
                    pass

        if "disabled_audio_indices" in updates:
            raw_list = updates["disabled_audio_indices"] or []
            clean_indices = sorted(
                {int(x) for x in raw_list if isinstance(x, int) or str(x).isdigit()}
            )
            set_fields["disabled_audio_indices"] = clean_indices

        if "audio_tracks" in updates and isinstance(updates["audio_tracks"], list):
            set_fields["audio_tracks"] = updates["audio_tracks"]

        if "hidden_for_users" in updates:
            set_fields["hidden_for_users"] = bool(updates["hidden_for_users"])

        if "tmdb_id" in updates:
            raw_tmdb = updates["tmdb_id"]
            if raw_tmdb is None or str(raw_tmdb).strip() == "":
                for k in ("tmdb_id", "tmdb_title", "tmdb_poster", "tmdb_year"):
                    unset_fields[k] = ""
            else:
                try:
                    set_fields["tmdb_id"] = int(raw_tmdb)
                    for tk in ("tmdb_title", "tmdb_poster", "tmdb_year"):
                        if tk in updates:
                            tv = str(updates[tk] or "").strip()
                            if tv:
                                set_fields[tk] = tv
                            else:
                                unset_fields[tk] = ""
                except (TypeError, ValueError):
                    pass

        set_fields["updated_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        op = {"$set": set_fields}
        if unset_fields:
            op["$unset"] = unset_fields

        await self.videos.update_one({"msg_id": msg_id}, op)
        return await self.videos.find_one({"msg_id": msg_id}, {"_id": 0})

    async def save_thumbnail(self, msg_id: int, jpeg_bytes: bytes):
        if not jpeg_bytes:
            return
        await self.thumbnails.update_one(
            {"msg_id": int(msg_id)},
            {
                "$set": {
                    "msg_id": int(msg_id),
                    "data": Binary(bytes(jpeg_bytes)),
                    "content_type": "image/jpeg",
                    "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
                }
            },
            upsert=True,
        )

    async def get_thumbnail(self, msg_id: int) -> Optional[bytes]:
        doc = await self.thumbnails.find_one(
            {"msg_id": int(msg_id)}, {"_id": 0, "data": 1}
        )
        if not doc:
            return None
        raw = doc.get("data")
        return bytes(raw) if raw else None

    async def delete_video(self, msg_id: int = None, stream_code: str = None):
        if msg_id is not None:
            res = await self.videos.delete_many({"msg_id": int(msg_id)})
            await self.thumbnails.delete_many({"msg_id": int(msg_id)})
            if res.deleted_count > 0:
                return True
        if stream_code:
            normalized = stream_code.strip().upper()
            doc = await self.videos.find_one(
                {"stream_code": normalized}, {"_id": 0, "msg_id": 1}
            )
            res = await self.videos.delete_many({"stream_code": normalized})
            if doc and doc.get("msg_id") is not None:
                await self.thumbnails.delete_many({"msg_id": int(doc["msg_id"])})
            return res.deleted_count > 0
        return False

    async def total_banned_count(self):
        return await self.bannedList.count_documents({})

    async def total_videos_count(self):
        return await self.videos.count_documents({})

    async def get_banned_ids(self):
        cursor = self.bannedList.find({}, {"banId": 1, "_id": 0})
        docs = await cursor.to_list(length=5000)
        return {int(d["banId"]) for d in docs if d.get("banId") is not None}

    async def get_users_paginated(
        self,
        query_text: str = None,
        limit: int = 30,
        skip: int = 0,
    ):
        query = {}
        if query_text and query_text.strip():
            q = query_text.strip().lstrip("@")
            safe_q = re.escape(q)
            pattern = {"$regex": safe_q, "$options": "i"}
            or_clauses = [
                {"name": pattern},
                {"username": pattern},
                {"join_date": pattern},
            ]
            if q.isdigit():
                or_clauses.append({"id": int(q)})
            query["$or"] = or_clauses
        max_items = max(1, min(int(limit), 100))
        offset = max(0, int(skip))
        cursor = (
            self.col.find(query, {"_id": 0})
            .sort([("join_date", -1), ("id", -1)])
            .skip(offset)
            .limit(max_items)
        )
        return await cursor.to_list(length=max_items)
    

    
