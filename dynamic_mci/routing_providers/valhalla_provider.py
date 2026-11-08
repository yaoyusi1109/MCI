import os
import requests

VALHALLA_URL = os.getenv('VALHALLA_URL','http://127.0.0.1:8002').rstrip('/')

def decode_polyline(encoded, precision=6):
    coords=[]; idx=0; lat=lon=0; scale=10**precision
    while idx < len(encoded):
        for axis in (0,1):
            result=shift=0
            while True:
                if idx>=len(encoded):
                    raise ValueError('Invalid Valhalla encoded route geometry')
                b=ord(encoded[idx])-63; idx+=1
                result|=(b&31)<<shift; shift+=5
                if b<32: break
            delta=~(result>>1) if result&1 else result>>1
            if axis==0: lat+=delta
            else: lon+=delta
        coords.append([lat/scale,lon/scale])
    return coords

def route(origin,destination):
    payload={'locations':[{'lat':origin[0],'lon':origin[1]},{'lat':destination[0],'lon':destination[1]}],
             'costing':'auto','units':'miles','shape_format':'polyline6'}
    response=requests.post(f'{VALHALLA_URL}/route',json=payload,timeout=35)
    response.raise_for_status()
    data=response.json()
    trip=data['trip']; summary=trip['summary']
    coords=[]
    for leg in trip['legs']:
        part=decode_polyline(leg['shape'])
        coords.extend(part if not coords else part[1:])
    return {'coordinates':coords,'travel_minutes':round(summary['time']/60,3),
            'distance_miles':round(summary['length'],3),'provider':'valhalla'}
