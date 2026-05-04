"""Union-Find for transitive merge grouping in the batch merger."""

from __future__ import annotations

from collections import defaultdict


class UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._rank: dict[str, int] = {}

    def add(self, item: str) -> None:
        if item not in self._parent:
            self._parent[item] = item
            self._rank[item] = 0

    def find(self, item: str) -> str:
        self.add(item)
        if self._parent[item] != item:
            self._parent[item] = self.find(self._parent[item])  # path compression
        return self._parent[item]

    def union(self, a: str, b: str) -> None:
        self.add(a)
        self.add(b)
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a == root_b:
            return
        # Union by rank
        if self._rank[root_a] < self._rank[root_b]:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a
        if self._rank[root_a] == self._rank[root_b]:
            self._rank[root_a] += 1

    def get_groups(self) -> list[list[str]]:
        """Return all groups (including singletons)."""
        buckets: dict[str, list[str]] = defaultdict(list)
        for item in self._parent:
            buckets[self.find(item)].append(item)
        return list(buckets.values())

    def get_merge_groups(self) -> list[list[str]]:
        """Return only groups with 2+ members (actual merge candidates)."""
        return [g for g in self.get_groups() if len(g) >= 2]
