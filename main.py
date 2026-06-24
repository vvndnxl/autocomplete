from __future__ import annotations

import threading
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

app = FastAPI(title="Autocomplete API")


def normalize(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()


class WordIn(BaseModel):
    term: str = Field(min_length=1, max_length=200)
    weight: int = Field(default=0, ge=0)


class WordsPayload(BaseModel):
    words: List[WordIn]


class WordOut(BaseModel):
    term: str
    weight: int


class AutocompleteOut(BaseModel):
    prefix: str
    suggestions: List[WordOut]


K = 50


@dataclass
class Record:
    display: str
    weight: int
    path: List["Node"]


class Node:
    __slots__ = ("children", "top", "term_map")

    children: Dict[str, Node]
    top: List[Tuple[int, str]]
    term_map: Dict[str, Tuple[int, str]]

    def __init__(self):
        self.children = {}
        self.top = []
        self.term_map = {}


# noinspection PyMethodMayBeStatic
class Store:
    def __init__(self):
        self.root = Node()
        self.records: Dict[str, Record] = {}
        self.lock = threading.Lock()

    def _path(self, key: str) -> List[Node]:
        node = self.root
        path = [node]
        for ch in key:
            node = node.children.setdefault(ch, Node())
            path.append(node)
        return path

    def _update_top(self, node: Node, term: str, weight: int):
        key = normalize(term)
        entry = (-weight, term)
        old_entry = node.term_map.get(key)

        if old_entry is not None:
            new_top = [x for x in node.top if x != old_entry]
        else:
            new_top = list(node.top)

        new_top.append(entry)
        new_top.sort()

        if len(new_top) > K:
            popped_entry = new_top.pop()
            popped_key = normalize(popped_entry[1])
            node.term_map.pop(popped_key, None)

        node.term_map[key] = entry
        node.top = new_top

    def _remove_from_top(self, node: Node, key: str):
        old_entry = node.term_map.pop(key, None)
        if old_entry is not None:
            node.top = [x for x in node.top if x != old_entry]

    def _upsert_locked(self, term: str, weight: int) -> int:
        key = normalize(term)

        if key in self.records:
            self._delete_locked(key)

        path = self._path(key)
        rec = Record(term, weight, path)
        self.records[key] = rec

        for node in path:
            self._update_top(node, term, weight)

        return len(self.records)

    def _delete_locked(self, key: str) -> bool:
        rec = self.records.get(key)
        if not rec:
            return False

        for node in rec.path:
            self._remove_from_top(node, key)

        del self.records[key]
        return True

    def upsert(self, term: str, weight: int) -> int:
        with self.lock:
            return self._upsert_locked(term, weight)

    def bulk_upsert(self, words: List[WordIn]) -> int:
        with self.lock:
            for w in words:
                self._upsert_locked(w.term, w.weight)
            return len(self.records)

    def get(self, term: str) -> Optional[WordOut]:
        key = normalize(term)
        with self.lock:
            rec = self.records.get(key)
            if not rec:
                return None
            return WordOut(term=rec.display, weight=rec.weight)

    def hit(self, term: str) -> WordOut:
        key = normalize(term)

        with self.lock:
            rec = self.records.get(key)

            if not rec:
                self._upsert_locked(term, 1)
                rec_obj = self.records[key]
                return WordOut(term=rec_obj.display, weight=rec_obj.weight)

            new_w = rec.weight + 1
            rec.weight = new_w
            rec.display = term

            for node in rec.path:
                self._update_top(node, term, new_w)

            return WordOut(term=rec.display, weight=new_w)

    def delete(self, term: str) -> bool:
        key = normalize(term)
        with self.lock:
            return self._delete_locked(key)

    def autocomplete(self, prefix: str, limit: int) -> dict:
        key = normalize(prefix)

        with self.lock:
            node = self.root
            for ch in key:
                node = node.children.get(ch)
                if node is None:
                    return {"prefix": prefix, "suggestions": []}

            target_top = node.top

        res = target_top[:limit]

        return {
            "prefix": prefix,
            "suggestions": [
                {"term": term, "weight": -weight}
                for weight, term in res
            ]
        }


store = Store()


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/words", status_code=201)
def bulk(payload: WordsPayload):
    total_count = store.bulk_upsert(payload.words)
    return {"count": total_count}


@app.get("/words/{term}")
def get_word(term: str):
    r = store.get(term)
    if not r:
        raise HTTPException(404)
    return r


@app.post("/words/{term}/hit")
def hit(term: str):
    return store.hit(term)


@app.delete("/words/{term}", status_code=204)
def delete(term: str):
    ok = store.delete(term)
    if not ok:
        raise HTTPException(404)


@app.get("/autocomplete")
def autocomplete(
        prefix: str = Query(..., min_length=1),
        limit: int = Query(10, ge=1, le=50)
):
    return store.autocomplete(prefix, limit)
