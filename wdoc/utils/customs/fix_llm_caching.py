"""
source : https://api.python.langchain.com/en/latest/_modules/langchain_community/cache.html#InMemoryCache

This workaround is to solve this: https://github.com/langchain-ai/langchain/issues/22389
Create a caching class that looks like it's just in memory but actually saves to sql

"""


import sqlite3
import json
import pickle
import datetime
from pathlib import Path, PosixPath
from typing import Union, Any, List, Optional
from threading import Lock

from langchain_core.caches import BaseCache

# use the same lock for each instance accessing the same db, as well as a
# global lock to create new locks
databases_locks = {"global": Lock()}
# also use the same cache
databases_caches = {}

SQLITE3_CHECK_SAME_THREAD=False

class SQLiteCacheFixed(BaseCache):
    """Cache that stores things in memory."""
    __VERSION__ = "0.5"

    def __init__(
        self,
        database_path: Union[str, PosixPath],
        expiration_days: Optional[int] = 0,
        ) -> None:
        dbp = Path(database_path)
        self.expiration_days = expiration_days
        # add versioning to avoid trying to use non backward compatible version
        self.database_path = dbp.parent / (dbp.stem + f"_v{self.__VERSION__}" + dbp.suffix)

        self.lockkey = str(self.database_path.absolute().resolve())
        if self.lockkey not in databases_locks:
            with databases_locks["global"]:
                databases_locks[self.lockkey] = Lock()
                databases_caches[self.lockkey] = {}
        self.lock = databases_locks[self.lockkey]
        with self.lock:
            with databases_locks["global"]:
                self._cache = databases_caches[self.lockkey]

        # create db
        conn = sqlite3.connect(self.database_path, check_same_thread=SQLITE3_CHECK_SAME_THREAD)
        cursor = conn.cursor()
        try:
            with self.lock:
                cursor.execute("BEGIN")
                cursor.execute('''CREATE TABLE IF NOT EXISTS storage (
                                key TEXT PRIMARY KEY,
                                data BLOB,
                                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                                )''')
                conn.commit()

                # Enable compression
                cursor.execute("PRAGMA page_size = 4096")
                cursor.execute("PRAGMA auto_vacuum = FULL")
                conn.commit()

                cursor.execute("VACUUM")
                conn.commit()
        finally:
            conn.close()


    def lookup(self, prompt: str, llm_string: str) -> Any:
        """Look up based on prompt and llm_string."""
        key = json.dumps((prompt, llm_string))

        return self.__get_data__(key=key)

    def __get_data__(self, key: str) -> Any:
        "actual lookup through cache or db"
        # check the cache is still as expected
        with databases_locks["global"]:
            assert id(self._cache) == id(databases_caches[self.lockkey])

        # already cached
        if key in self._cache:
            return self._cache[key]

        # load the value from the db
        conn = sqlite3.connect(self.database_path, check_same_thread=SQLITE3_CHECK_SAME_THREAD)
        cursor = conn.cursor()
        try:
            with self.lock:
                cursor.execute("BEGIN")
                cursor.execute('SELECT data FROM storage WHERE key = ?', (key,))
                cursor.execute("UPDATE storage SET timestamp = CURRENT_TIMESTAMP WHERE key = ?", (key,))
                conn.commit()
                result = cursor.fetchone()

        finally:
            conn.close()
        if result:
            result = result[0]
        if result:
            result = pickle.loads(result)
            with self.lock:
                self._cache[key] = result
        return result

    def update(self, prompt: str, llm_string: str, return_val: Any) -> None:
        """Update cache based on prompt and llm_string."""
        key = json.dumps((prompt, llm_string))

        self.__set_data__(key=key, data=return_val)


    def __set_data__(self, key: str, data: Any) -> None:
        "actual code to set the data in the db then the cache"
        if key in self._cache and self._cache[key] == data:
            return

        conn = sqlite3.connect(self.database_path, check_same_thread=SQLITE3_CHECK_SAME_THREAD)
        cursor = conn.cursor()
        compressed = pickle.dumps(data)
        try:
            with self.lock:
                cursor.execute("BEGIN")
                cursor.execute("INSERT OR REPLACE INTO storage (key, data, timestamp) VALUES (?, ?, CURRENT_TIMESTAMP)", (key, compressed))
                conn.commit()
                self._cache[key] = data
        finally:
            conn.close()


    def clear(self) -> None:
        """Clear cache."""
        with self.lock:
            self._cache.clear()


    async def alookup(self, prompt: str, llm_string: str) -> Any:
        """Look up based on prompt and llm_string."""
        return self.lookup(prompt, llm_string)


    async def aupdate(
        self, prompt: str, llm_string: str, return_val: Any
    ) -> None:
        """Update cache based on prompt and llm_string."""
        self.update(prompt, llm_string, return_val)


    async def aclear(self) -> None:
        """Clear cache."""
        self.clear()

    def __get_keys__(self) -> List[str]:
        "get the list of keys present in the db"
        # load the value from the db
        conn = sqlite3.connect(self.database_path, check_same_thread=SQLITE3_CHECK_SAME_THREAD)
        cursor = conn.cursor()
        try:
            with self.lock:
                cursor.execute('SELECT key FROM storage')
                results = [row[0] if row else None for row in cursor.fetchall()]
        finally:
            conn.close()
        for r in results:
            yield r

    def __expire__(self, verbose: bool = False) -> None:
        "get the list of keys present in the db"
        if not self.expiration_days:
            return
        assert self.expiration_days > 0, "expiration_days has to be a positive int or 0 to disable"
        expiration_date = datetime.datetime.now() - datetime.timedelta(days=self.expiration_days)

        if verbose:
            lenbefore = len(list(self.__get_keys__()))

        conn = sqlite3.connect(self.database_path, check_same_thread=SQLITE3_CHECK_SAME_THREAD)
        cursor = conn.cursor()
        try:
            with self.lock:
                cursor.execute("BEGIN")
                cursor.execute("DELETE FROM storage WHERE timestamp < ?", (expiration_date,))
                conn.commit()

                cursor.execute("VACUUM")
                conn.commit()
        finally:
            conn.close()

        if verbose:
            lenafter = len(list(self.__get_keys__()))
            diff = lenbefore - lenafter
            print(f"Removed {diff} entries of cache. Remaining: {lenafter}")

    def __get_columns__(self) -> List[str]:
        "get the list of column in the db"
        conn = sqlite3.connect(self.database_path, check_same_thread=SQLITE3_CHECK_SAME_THREAD)
        cursor = conn.cursor()
        try:
            with self.lock:
                cursor.execute("PRAGMA table_info(storage)")
                columns = [column[1] for column in cursor.fetchall()]
        finally:
            conn.close()
        assert columns == ["key", "data", "timestamp"], columns
        return columns


if "__main__" == __name__:
    import code

    Path("test.sql").unlink(missing_ok=True)
    lfs = SQLiteCacheFixed("test.sql", expiration_days=1)
    lfs.__expire__()
    lfs.__set_data__(key="test", data=5)
    assert "test" in list(lfs.__get_keys__())
    lfs.__set_data__(key="test", data=7)
    print(lfs.__get_data__(key="test"))
    code.interact(local=locals())
