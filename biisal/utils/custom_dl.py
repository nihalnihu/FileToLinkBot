import math
import asyncio
import logging
from biisal.vars import Var
from typing import Any, Dict, List, Optional, Tuple, Union
from biisal.bot import work_loads
from pyrogram import Client, utils, raw
from .file_properties import get_file_ids
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid
from biisal.server.exceptions import FIleNotFound
from pyrogram.file_id import FileId, FileType, ThumbnailSource


class ByteStreamer:
    def __init__(self, client: Client):
        """A custom class that holds the cache of a specific client and class functions.
        attributes:
            client: the client that the cache is for.
            cached_file_ids: a dict of cached file IDs.
            cached_file_properties: a dict of cached file properties.
        
        functions:
            generate_file_properties: returns the properties for a media of a specific message contained in Tuple.
            generate_media_session: returns the media session for the DC that contains the media file.
            yield_file: yield a file from telegram servers for streaming.
            
        This is a modified version of the <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/telegram/utils/custom_download.py>
        Thanks to Eyaadh <https://github.com/eyaadh>
        """
        self.clean_timer = 30 * 60
        self.client: Client = client
        self.cached_file_ids: Dict[int, FileId] = {}
        self._session_locks: Dict[tuple, asyncio.Lock] = {}
        asyncio.create_task(self.clean_cache())

    def _get_session_lock(self, client: Client, dc_id: int) -> asyncio.Lock:
        key = (id(client), dc_id)
        lock = self._session_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[key] = lock
        return lock

    @staticmethod
    def _is_session_healthy(media_session: Session) -> bool:
        if media_session is None:
            return False
        is_connected = getattr(media_session, "is_connected", None)
        if is_connected is None or not is_connected.is_set():
            return False
        connection = getattr(media_session, "connection", None)
        protocol = getattr(connection, "protocol", None) if connection is not None else None
        if protocol is None:
            return False
        if hasattr(protocol, "encrypt") and getattr(protocol, "encrypt", None) is None:
            return False
        return True

    @staticmethod
    async def _safe_stop_session(media_session: Session) -> None:
        if media_session is None:
            return
        try:
            await media_session.stop()
        except Exception as err:
            logging.debug(f"Ignoring error while stopping stale media session: {err}")

    async def _invalidate_media_session(
        self, client: Client, dc_id: int, stale_session: Session
    ) -> None:
        async with self._get_session_lock(client, dc_id):
            current = client.media_sessions.get(dc_id)
            if current is stale_session:
                client.media_sessions.pop(dc_id, None)
                await self._safe_stop_session(stale_session)

    @staticmethod
    def _consume_task_exception(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    async def get_file_properties(self, id: int) -> FileId:
        """
        Returns the properties of a media of a specific message in a FIleId class.
        if the properties are cached, then it'll return the cached results.
        or it'll generate the properties from the Message ID and cache them.
        """
        if id not in self.cached_file_ids:
            await self.generate_file_properties(id)
            logging.debug(f"Cached file properties for message with ID {id}")
        return self.cached_file_ids[id]
    
    async def generate_file_properties(self, id: int) -> FileId:
        """
        Generates the properties of a media file on a specific message.
        returns ths properties in a FIleId class.
        """
        file_id = await get_file_ids(self.client, Var.BIN_CHANNEL, id)
        logging.debug(f"Generated file ID and Unique ID for message with ID {id}")
        if not file_id:
            logging.debug(f"Message with ID {id} not found")
            raise FIleNotFound
        self.cached_file_ids[id] = file_id
        logging.debug(f"Cached media message with ID {id}")
        return self.cached_file_ids[id]

    async def generate_media_session(self, client: Client, file_id: FileId) -> Session:
        """
        Generates the media session for the DC that contains the media file.
        This is required for getting the bytes from Telegram servers.
        """
        async with self._get_session_lock(client, file_id.dc_id):
            media_session = client.media_sessions.get(file_id.dc_id, None)

            if media_session is not None and not self._is_session_healthy(media_session):
                is_connected = getattr(media_session, "is_connected", None)
                if is_connected is not None:
                    try:
                        await asyncio.wait_for(is_connected.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass
                if not self._is_session_healthy(media_session):
                    logging.warning(
                        f"Cached media session for DC {file_id.dc_id} is unhealthy; recreating."
                    )
                    client.media_sessions.pop(file_id.dc_id, None)
                    await self._safe_stop_session(media_session)
                    media_session = None

            if media_session is None:
                if file_id.dc_id != await client.storage.dc_id():
                    media_session = Session(
                        client,
                        file_id.dc_id,
                        await asyncio.wait_for(
                            Auth(
                                client, file_id.dc_id, await client.storage.test_mode()
                            ).create(),
                            timeout=12.0,
                        ),
                        await client.storage.test_mode(),
                        is_media=True,
                    )
                    await asyncio.wait_for(media_session.start(), timeout=12.0)

                    try:
                        for _ in range(6):
                            exported_auth = await asyncio.wait_for(
                                client.invoke(
                                    raw.functions.auth.ExportAuthorization(
                                        dc_id=file_id.dc_id
                                    )
                                ),
                                timeout=8.0,
                            )

                            try:
                                await asyncio.wait_for(
                                    media_session.invoke(
                                        raw.functions.auth.ImportAuthorization(
                                            id=exported_auth.id, bytes=exported_auth.bytes
                                        ),
                                        retries=1,
                                        timeout=8.0,
                                    ),
                                    timeout=10.0,
                                )
                                break
                            except AuthBytesInvalid:
                                logging.debug(
                                    f"Invalid authorization bytes for DC {file_id.dc_id}"
                                )
                                continue
                        else:
                            raise AuthBytesInvalid
                    except Exception:
                        await self._safe_stop_session(media_session)
                        raise
                else:
                    media_session = Session(
                        client,
                        file_id.dc_id,
                        await client.storage.auth_key(),
                        await client.storage.test_mode(),
                        is_media=True,
                    )
                    await asyncio.wait_for(media_session.start(), timeout=12.0)
                logging.debug(f"Created media session for DC {file_id.dc_id}")
                client.media_sessions[file_id.dc_id] = media_session
            else:
                logging.debug(f"Using cached media session for DC {file_id.dc_id}")
            return media_session


    @staticmethod
    async def get_location(file_id: FileId) -> Union[raw.types.InputPhotoFileLocation,
                                                     raw.types.InputDocumentFileLocation,
                                                     raw.types.InputPeerPhotoFileLocation,]:
        """
        Returns the file location for the media file.
        """
        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id, access_hash=file_id.chat_access_hash
                )
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                else:
                    peer = raw.types.InputPeerChannel(
                        channel_id=utils.get_channel_id(file_id.chat_id),
                        access_hash=file_id.chat_access_hash,
                    )

            location = raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG,
            )
        elif file_type == FileType.PHOTO:
            location = raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        else:
            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        return location

    async def yield_file(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int,
    ) -> Union[str, None]:
        """
        Custom generator that yields the bytes of the media file.
        Modded from <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/telegram/utils/custom_download.py#L20>
        Thanks to Eyaadh <https://github.com/eyaadh>
        """
        client = self.client
        work_loads[index] += 1
        logging.debug(f"Starting to yielding file with client {index}.")

        current_part = 1
        location = None
        prefetch_ahead = 1 if part_count <= 2 else 6
        pending_tasks: Dict[int, asyncio.Task] = {}

        async def _drain_pending() -> None:
            if not pending_tasks:
                return
            tasks = list(pending_tasks.values())
            pending_tasks.clear()
            for pending in tasks:
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        def _schedule_part(session: Session, part_idx: int) -> None:
            if part_idx > part_count or part_idx in pending_tasks:
                return
            part_offset = offset + (part_idx - 1) * chunk_size
            task = asyncio.create_task(
                session.invoke(
                    raw.functions.upload.GetFile(
                        location=location, offset=part_offset, limit=chunk_size
                    ),
                    retries=1,
                    timeout=6.0,
                )
            )
            task.add_done_callback(self._consume_task_exception)
            pending_tasks[part_idx] = task

        def _prime_prefetch(session: Session, start_part: int) -> None:
            for p in range(
                start_part, min(part_count, start_part + prefetch_ahead - 1) + 1
            ):
                _schedule_part(session, p)

        try:
            location = await self.get_location(file_id)
            media_session = await self.generate_media_session(client, file_id)
            _prime_prefetch(media_session, current_part)
            session_recoveries = 0

            while current_part <= part_count:
                task = pending_tasks.pop(current_part, None)
                if task is None:
                    break
                try:
                    r = await asyncio.wait_for(task, timeout=10.0)
                    session_recoveries = 0
                except (
                    TimeoutError,
                    asyncio.TimeoutError,
                    AttributeError,
                    OSError,
                    TypeError,
                    ConnectionError,
                ) as err:
                    if not task.done():
                        task.cancel()
                    await _drain_pending()
                    await self._invalidate_media_session(
                        client, file_id.dc_id, media_session
                    )
                    if session_recoveries >= 2:
                        logging.warning(
                            f"Stopping stream for DC {file_id.dc_id} part {current_part} after session error: {err}"
                        )
                        break
                    session_recoveries += 1
                    logging.warning(
                        f"Recovering media session for DC {file_id.dc_id} at part {current_part} ({session_recoveries}/2): {err}"
                    )
                    media_session = await self.generate_media_session(client, file_id)
                    _prime_prefetch(media_session, current_part)
                    continue

                _schedule_part(media_session, current_part + prefetch_ahead)

                if not isinstance(r, raw.types.upload.File):
                    break
                chunk = r.bytes
                if not chunk:
                    break
                if part_count == 1:
                    sliced = chunk[first_part_cut:last_part_cut]
                elif current_part == 1:
                    sliced = chunk[first_part_cut:]
                elif current_part == part_count:
                    sliced = chunk[:last_part_cut]
                else:
                    sliced = chunk
                if not sliced:
                    break
                yield sliced

                current_part += 1
        except (GeneratorExit, asyncio.CancelledError):
            raise
        except (
            TimeoutError,
            asyncio.TimeoutError,
            AttributeError,
            OSError,
            TypeError,
            ConnectionError,
        ) as err:
            logging.warning(f"Stream interrupted while yielding file: {err}")
        finally:
            try:
                for task in pending_tasks.values():
                    if not task.done():
                        task.cancel()
                if pending_tasks:
                    await asyncio.gather(
                        *pending_tasks.values(), return_exceptions=True
                    )
                    pending_tasks.clear()
            finally:
                logging.debug(f"Finished yielding file with {current_part} parts.")
                work_loads[index] -= 1

    
    async def clean_cache(self) -> None:
        """
        function to clean the cache to reduce memory usage
        """
        while True:
            await asyncio.sleep(self.clean_timer)
            self.cached_file_ids.clear()
            logging.debug("Cleaned the cache")


class _StripedWorker:
    __slots__ = ("index", "streamer", "file_id", "location", "media_session")

    def __init__(
        self,
        index: int,
        streamer: ByteStreamer,
        file_id: FileId,
        location: Any,
        media_session: Session,
    ):
        self.index = index
        self.streamer = streamer
        self.file_id = file_id
        self.location = location
        self.media_session = media_session


async def yield_striped_file(
    msg_id: int,
    primary_streamer: ByteStreamer,
    primary_index: int,
    primary_file_id: FileId,
    streamers_by_index: Dict[int, ByteStreamer],
    offset: int,
    first_part_cut: int,
    last_part_cut: int,
    part_count: int,
    chunk_size: int,
):
    """Stripes Telegram `GetFile` chunk fetches across multiple bot clients
    simultaneously and yields bytes strictly in sequential order."""
    if part_count <= 0:
        return

    ordered_indices = [primary_index] + [
        idx
        for idx in sorted(
            streamers_by_index.keys(),
            key=lambda i: work_loads.get(i, 0),
        )
        if idx != primary_index
    ]

    primary_loc = await primary_streamer.get_location(primary_file_id)
    primary_sess = await primary_streamer.generate_media_session(
        primary_streamer.client, primary_file_id
    )
    active_workers: List[_StripedWorker] = [
        _StripedWorker(
            primary_index, primary_streamer, primary_file_id, primary_loc, primary_sess
        )
    ]
    acquired_indices: List[int] = [primary_index]
    acquired_set = {primary_index}
    work_loads[primary_index] = work_loads.get(primary_index, 0) + 1

    secondary_init_tasks: List[asyncio.Task] = []
    stream_closed = False

    async def _attach_secondary(idx: int, streamer: ByteStreamer) -> None:
        try:
            fid = await asyncio.wait_for(
                streamer.get_file_properties(msg_id), timeout=8.0
            )
            loc = await streamer.get_location(fid)
            sess = await asyncio.wait_for(
                streamer.generate_media_session(streamer.client, fid),
                timeout=10.0,
            )
            if stream_closed or idx in acquired_set:
                return
            w = _StripedWorker(idx, streamer, fid, loc, sess)
            active_workers.append(w)
            acquired_set.add(idx)
            acquired_indices.append(idx)
            work_loads[idx] = work_loads.get(idx, 0) + 1
        except asyncio.CancelledError:
            pass
        except Exception as err:
            logging.warning(f"Skipping striped worker {idx} for msg {msg_id}: {err}")

    for idx in ordered_indices:
        if idx == primary_index:
            continue
        streamer = streamers_by_index[idx]
        cached_fid = streamer.cached_file_ids.get(msg_id)
        cached_sess = (
            streamer.client.media_sessions.get(cached_fid.dc_id)
            if cached_fid is not None
            else None
        )
        if (
            cached_fid is not None
            and ByteStreamer._is_session_healthy(cached_sess)
            and idx not in acquired_set
        ):
            loc = await streamer.get_location(cached_fid)
            active_workers.append(
                _StripedWorker(idx, streamer, cached_fid, loc, cached_sess)
            )
            acquired_set.add(idx)
            acquired_indices.append(idx)
            work_loads[idx] = work_loads.get(idx, 0) + 1
        else:
            t = asyncio.create_task(_attach_secondary(idx, streamer))
            t.add_done_callback(ByteStreamer._consume_task_exception)
            secondary_init_tasks.append(t)

    def _current_prefetch_ahead() -> int:
        return min(max(2, len(active_workers) * 2), 8)

    pending_tasks: Dict[int, Tuple[asyncio.Task, _StripedWorker]] = {}
    current_part = 1

    def _schedule_part(part_idx: int) -> None:
        if part_idx > part_count or part_idx in pending_tasks or not active_workers:
            return
        worker = active_workers[(part_idx - 1) % len(active_workers)]
        part_offset = offset + (part_idx - 1) * chunk_size
        task = asyncio.create_task(
            worker.media_session.invoke(
                raw.functions.upload.GetFile(
                    location=worker.location,
                    offset=part_offset,
                    limit=chunk_size,
                ),
                retries=1,
                timeout=6.0,
            )
        )
        task.add_done_callback(ByteStreamer._consume_task_exception)
        pending_tasks[part_idx] = (task, worker)

    try:
        for p in range(1, min(part_count, _current_prefetch_ahead()) + 1):
            _schedule_part(p)

        recoveries = 0
        while current_part <= part_count:
            entry = pending_tasks.pop(current_part, None)
            if entry is None:
                if not active_workers:
                    break
                _schedule_part(current_part)
                entry = pending_tasks.pop(current_part, None)
                if entry is None:
                    break

            task, worker = entry
            try:
                r = await asyncio.wait_for(task, timeout=10.0)
                recoveries = 0
            except Exception as err:
                if not task.done():
                    task.cancel()
                await worker.streamer._invalidate_media_session(
                    worker.streamer.client,
                    worker.file_id.dc_id,
                    worker.media_session,
                )
                if len(active_workers) > 1 or (worker not in active_workers and active_workers):
                    if worker in active_workers:
                        active_workers.remove(worker)
                    logging.warning(
                        f"Worker {worker.index} failed at part {current_part}; failing over ({len(active_workers)} left): {err}"
                    )
                    continue

                if recoveries >= 2:
                    logging.warning(
                        f"Stopping striped stream for msg {msg_id} at part {current_part}: {err}"
                    )
                    break
                recoveries += 1
                worker.media_session = await worker.streamer.generate_media_session(
                    worker.streamer.client, worker.file_id
                )
                continue

            ahead = _current_prefetch_ahead()
            for p in range(current_part + 1, min(part_count, current_part + ahead) + 1):
                _schedule_part(p)

            if not isinstance(r, raw.types.upload.File):
                break
            chunk = r.bytes
            if not chunk:
                break
            if part_count == 1:
                sliced = chunk[first_part_cut:last_part_cut]
            elif current_part == 1:
                sliced = chunk[first_part_cut:]
            elif current_part == part_count:
                sliced = chunk[:last_part_cut]
            else:
                sliced = chunk
            if not sliced:
                break
            yield sliced

            current_part += 1
    except (GeneratorExit, asyncio.CancelledError):
        raise
    except Exception as err:
        logging.warning(f"Striped stream interrupted at part {current_part}: {err}")
    finally:
        stream_closed = True
        try:
            tasks_to_drain = []
            for task, _ in pending_tasks.values():
                if not task.done():
                    task.cancel()
                tasks_to_drain.append(task)
            pending_tasks.clear()
            for stask in secondary_init_tasks:
                if not stask.done():
                    stask.cancel()
                tasks_to_drain.append(stask)
            secondary_init_tasks.clear()
            if tasks_to_drain:
                await asyncio.gather(*tasks_to_drain, return_exceptions=True)
        finally:
            for idx in acquired_indices:
                work_loads[idx] = max(0, work_loads.get(idx, 1) - 1)