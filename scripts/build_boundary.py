"""Reproducible transcription of treaty vertices and great-circle interpolation.

Only classify within the app's Tamil Nadu study rectangle. Coordinates are not
a surveyed WGS84 navigation dataset; preserve the provenance and uncertainty.
"""
import json
import math
from pathlib import Path

def dm(deg, minute):
    return deg + minute / 60

# South to north. (id, latitude degrees/minutes, longitude degrees/minutes)
MANNAR = [("13m",5,0,77,10.6),("12m",5,53.9,77,50.7),("11m",6,30.8,78,12.2),
          ("10m",7,21,78,38.8),("9m",7,35.3,78,45.7),("8m",8,12.2,78,53.7),
          ("7m",8,22.2,78,55.4),("6m",8,31.2,79,4.7),("5m",8,37.2,79,13),
          ("4m",8,40,79,18.2),("3m",8,53.8,79,29.3),("2m",9,0,79,31.3),("1m/6",9,6,79,32)]
PALK = [("5",9,13,79,32),("4",9,21.8,79,30.7),("3",9,40.15,79,22.6),
        ("2",9,57,79,35),("1/1b",10,5,80,3)]
BENGAL = [("1ba",10,5.8,80,5),("1bb",10,8.4,80,9.5),("2b",10,33,80,46),
          ("3b",10,41.7,81,2.5),("4b",11,2.7,81,56),("5b",11,16,82,24.4),("6b",11,26.6,83,22)]

def vector(p):
    lat,lon=map(math.radians,p)
    return [math.cos(lat)*math.cos(lon),math.cos(lat)*math.sin(lon),math.sin(lat)]

def build():
    vertices=[{"id":p[0],"latitude":dm(p[1],p[2]),"longitude":dm(p[3],p[4])} for p in MANNAR+PALK+BENGAL]
    points=[]
    for a,b in zip(vertices,vertices[1:]):
        u,v=vector([a['latitude'],a['longitude']]),vector([b['latitude'],b['longitude']])
        angle=math.acos(max(-1,min(1,sum(x*y for x,y in zip(u,v)))))
        steps=max(1,math.ceil(angle*6371.0088/0.5))
        for i in range(steps):
            t=i/steps
            w=[(math.sin((1-t)*angle)*x+math.sin(t*angle)*y)/math.sin(angle) for x,y in zip(u,v)]
            points.append([math.degrees(math.asin(w[2])),math.degrees(math.atan2(w[1],w[0]))])
    points.append([vertices[-1]['latitude'],vertices[-1]['longitude']])
    data={"version":"india-sri-lanka-treaty-1974-1976-v1","name":"India–Sri Lanka maritime boundary",
          "coverage":{"south":7,"north":14,"west":77,"east":81},"near_km":2,"planning_buffer_km":0.5,
          "sources":[{"title":"1974 boundary agreement, Article 1 (MEA reproduction)","url":"https://www.mea.gov.in/Images/pdf/RTI_Aladigurusamy_80014_v1.pdf"},
                     {"title":"1976 maritime boundary agreement, Articles 1–2 (UN treaty text)","url":"https://www.un.org/depts/los/LEGISLATIONANDTREATIES/PDFFILES/TREATIES/LKA-IND1976MB.PDF"},
                     {"title":"Accessible mirror of the UN treaty text","url":"https://www.marineregions.org/documents/LKA-IND1976MB.PDF"}],
          "method":"Treaty coordinates with great-circle interpolation at ≤500 m; regional side and proximity checks against the interpolated line.",
          "limitations":"Research approximation, not an official electronic navigation chart. Historical datum/survey transformations are not available. The 500 m planning buffer is a project choice, not a legal tolerance. Only this bilateral boundary within the study rectangle is checked; not fishing permissions, all maritime zones, or land.",
          "vertices":vertices,"coordinates":points}
    output=Path(__file__).resolve().parents[1]/'web/data/maritime-boundary.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(data,separators=(',',':')))

if __name__=='__main__': build()
