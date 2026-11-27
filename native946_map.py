"""Read native map collision cells and validate native direction paths."""

from functools import lru_cache
from heapq import heappop, heappush
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).with_name(".deps")))
import lzokay


# Verified against the 3.0.946 link_wp direction matrix (RVA 0xDD9110).
DIRECTIONS = {ord(ch): delta for ch, delta in zip("01234567",
    ((1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0)))}


class MapGrid:
    def __init__(self, filename):
        with Path(filename).open("rb") as stream:
            magic, cell_offset, width, height, cw, ch = struct.unpack("<4sIIIHH", stream.read(20))
            if magic != b"0.3M" or (cw, ch) != (20, 20):
                raise ValueError("Unexpected native map format")
            # Native indoor maps include a partial final collision cell.
            self.width, self.height = (width+cw-1)//cw, (height+ch-1)//ch
            stream.seek(cell_offset)
            tag, size = struct.unpack("<4sI", stream.read(8))
            if tag != b"LLEC" or size > 16 << 20:
                raise ValueError("Invalid native collision data")
            self.cells = lzokay.decompress(stream.read(size), self.width * self.height)

    def walkable(self, x, y):
        return (0 <= x < self.width and 0 <= y < self.height
                and not bool(self.cells[y * self.width + x] & 1))

    def nearest_walkable(self, x, y):
        for radius in range(33):
            candidates = [(dx*dx+dy*dy, x+dx, y+dy)
                          for dx in range(-radius, radius+1) for dy in range(-radius, radius+1)
                          if max(abs(dx), abs(dy)) == radius and self.walkable(x+dx, y+dy)]
            if candidates:
                _, px, py = min(candidates)
                return px, py
        raise ValueError("No walkable cell near the stored position")

    def path(self, start, directions):
        if not self.walkable(*start) or len(directions) > 256:
            raise ValueError("Invalid movement start or path length")
        result = [start]
        x, y = start
        for code in directions:
            if code not in DIRECTIONS:
                raise ValueError("Invalid native direction code")
            dx, dy = DIRECTIONS[code]
            if not self.walkable(x + dx, y + dy):
                raise ValueError("Movement crosses a blocked cell")
            x, y = x + dx, y + dy
            result.append((x, y))
        return result

    def nearby(self, x, y, radius=6):
        for distance in range(2, radius + 1):
            for dx, dy in ((distance, 0), (0, distance), (-distance, 0), (0, -distance)):
                if self.walkable(x + dx, y + dy):
                    return x + dx, y + dy
        raise ValueError("No walkable NPC position near character")

    def route(self, start, end):
        if not self.walkable(*start) or not self.walkable(*end):
            raise ValueError("Movement endpoint is blocked")
        if max(abs(start[0]-end[0]), abs(start[1]-end[1])) > 64:
            raise ValueError("Movement is too far for one request")
        queue = [(0, 0, start)]
        costs, parents = {start: 0}, {}
        while queue and len(costs) <= 8192:
            _, cost, point = heappop(queue)
            if cost != costs[point]:
                continue
            if point == end:
                path = [end]
                while path[-1] != start:
                    path.append(parents[path[-1]])
                return list(reversed(path))
            x, y = point
            for dx, dy in DIRECTIONS.values():
                nxt = x + dx, y + dy
                if not self.walkable(*nxt):
                    continue
                if dx and dy and (not self.walkable(x+dx, y) or not self.walkable(x, y+dy)):
                    continue
                candidate = cost + (14 if dx and dy else 10)
                if candidate < costs.get(nxt, 1 << 30):
                    costs[nxt], parents[nxt] = candidate, point
                    heuristic = 10 * max(abs(nxt[0]-end[0]), abs(nxt[1]-end[1]))
                    heappush(queue, (candidate + heuristic, candidate, nxt))
        raise ValueError("No walkable route to movement target")


@lru_cache(maxsize=192)
def load_map(map_id):
    from native946_world import world
    resource=world().scene(map_id)['map_id']
    return MapGrid(Path(__file__).with_name("client-946") / "map" / f"{resource}.map")
