"""Regional advisory boundary geometry shared with the offline browser data."""
import json
from pathlib import Path
import numpy as np

DATA = json.loads((Path(__file__).resolve().parents[1]/'web/data/maritime-boundary.json').read_text())
LINE = np.array(DATA['coordinates'])  # latitude, longitude; monotonically northward

def status(latitude, longitude, accuracy_m=0):
    if not all(np.isfinite([latitude,longitude,accuracy_m])) or accuracy_m < 0:
        raise ValueError('Finite coordinates and nonnegative GPS accuracy are required.')
    c=DATA['coverage']
    if not(c['south']<=latitude<=c['north'] and c['west']<=longitude<=c['east']):
        return {'status':'unknown','side':'unknown','distance_km':None,'message':'Outside boundary-monitor coverage. Border status is unknown.'}
    boundary_lon=np.interp(latitude,LINE[:,0],LINE[:,1],left=LINE[0,1],right=LINE[-1,1])
    side='india' if longitude<boundary_lon else 'sri_lanka'
    # Local equirectangular distance, adequate for the advisory buffer; not survey-grade.
    scale=np.array([111.195,111.195*np.cos(np.radians(latitude))])
    a=(LINE[:-1]-[latitude,longitude])*scale
    ab=np.diff(LINE,axis=0)*scale
    t=np.clip(-np.sum(a*ab,axis=1)/np.sum(ab*ab,axis=1),0,1)
    distance=float(np.linalg.norm(a+t[:,None]*ab,axis=1).min())
    uncertainty=accuracy_m/1000+DATA['planning_buffer_km']
    if distance<=uncertainty:
        state,message='uncertain','At or near the border: GPS accuracy and map uncertainty prevent a reliable side determination.'
    elif side=='sri_lanka':
        state,message='outside','Border warning: position is on the Sri Lankan side of the mapped maritime boundary.'
    elif distance<=DATA['near_km']+accuracy_m/1000:
        state,message='near','Approaching maritime border. Stay clear of the boundary.'
    else:
        state,message='inside','Position is on the Indian side of the mapped boundary.'
    return {'status':state,'side':side,'distance_km':distance,'accuracy_m':accuracy_m,'message':message}

def intersects(a,b):
    """Inclusive segment intersection; catches exits/re-entry between endpoints."""
    a,b=np.asarray(a),np.asarray(b)
    c,d=LINE[:-1],LINE[1:]
    def cross(u,v): return u[...,0]*v[...,1]-u[...,1]*v[...,0]
    o1,o2=cross(b-a,c-a),cross(b-a,d-a)
    o3,o4=cross(d-c,a-c),cross(d-c,b-c)
    overlap=np.all(np.maximum(np.minimum(a,b),np.minimum(c,d))<=np.minimum(np.maximum(a,b),np.maximum(c,d))+1e-10,axis=1)
    return bool(np.any((o1*o2<=1e-18)&(o3*o4<=1e-18)&overlap))

def allowed(point):
    s=status(*point)
    return s['side']=='india' and s['distance_km'] is not None and s['distance_km']>DATA['planning_buffer_km']

def segment_allowed(a,b):
    if intersects(a,b): return False
    # Check the buffer throughout, not just endpoints. 250 m sampling plus a
    # 125 m margin ensures a missed between-sample minimum cannot cross 500 m.
    count=max(2,int(np.ceil(np.linalg.norm((np.asarray(a)-b)*111.195)/.25))+1)
    for p in np.linspace(a,b,count):
        s=status(*p)
        if s['side']!='india' or s['distance_km'] is None or s['distance_km']<=DATA['planning_buffer_km']+.125:
            return False
    return True

def assess_path(points):
    states=[status(*p) for p in points]
    crosses=any(intersects(a,b) for a,b in zip(points,points[1:]))
    outside=any(s['side']=='sri_lanka' for s in states)
    unknown=any(s['status']=='unknown' for s in states)
    return {'crosses_boundary':crosses,'has_foreign_side':outside,'unknown':unknown,
            'warning':crosses or outside or unknown or any(s['status'] in ['near','uncertain'] for s in states),
            'message':'Border warning: the proposed path crosses the mapped India–Sri Lanka boundary or reaches the Sri Lankan side. Choose points on the Indian side.' if crosses or outside else ('Boundary check unavailable outside the study region.' if unknown else 'No border crossing detected on this path.'),
            'source_version':DATA['version']}
