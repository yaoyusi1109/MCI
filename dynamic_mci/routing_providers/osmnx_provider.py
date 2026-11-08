from functools import lru_cache
from pathlib import Path
import networkx as nx
import osmnx as ox

GRAPH_PATH = Path(__file__).resolve().parents[1] / 'data' / 'baltimore_city_drive.graphml'

@lru_cache(maxsize=1)
def graph():
    if not GRAPH_PATH.exists():
        raise RuntimeError(f'OSMnx graph missing: {GRAPH_PATH}. Copy your cached Baltimore City GraphML here.')
    return ox.load_graphml(GRAPH_PATH)

def route(origin, destination):
    G = graph()
    start = ox.distance.nearest_nodes(G, X=origin[1], Y=origin[0])
    end = ox.distance.nearest_nodes(G, X=destination[1], Y=destination[0])
    nodes = nx.shortest_path(G, start, end, weight='travel_time')
    coords = []
    seconds = meters = 0.0
    for u, v in zip(nodes[:-1], nodes[1:]):
        edge = min(G[u][v].values(), key=lambda e: float(e['travel_time']))
        seconds += float(edge['travel_time'])
        meters += float(edge['length'])
        if edge.get('geometry') is not None:
            segment = [[float(y), float(x)] for x, y in edge['geometry'].coords]
            uy, ux = float(G.nodes[u]['y']), float(G.nodes[u]['x'])
            if (segment[-1][0]-uy)**2 + (segment[-1][1]-ux)**2 < (segment[0][0]-uy)**2 + (segment[0][1]-ux)**2:
                segment.reverse()
        else:
            segment = [[float(G.nodes[n]['y']), float(G.nodes[n]['x'])] for n in (u,v)]
        if coords and coords[-1] == segment[0]:
            coords.extend(segment[1:])
        else:
            coords.extend(segment)
    if not coords:
        coords = [[float(G.nodes[start]['y']),float(G.nodes[start]['x'])]]
    return {'coordinates':coords,'travel_minutes':round(seconds/60,3),'distance_miles':round(meters/1609.344,3),'provider':'osmnx'}
