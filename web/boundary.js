/* The same densified treaty line is used by Python and the browser. No GPS
 * coordinates leave the device for these checks; works with a cached shell. */
(function(root){
  function assess(data,lat,lon,accuracy=0){
    if(![lat,lon,accuracy].every(Number.isFinite)||accuracy<0)return {status:'unknown',side:'unknown',message:'Invalid GPS fix; border status unknown.'};
    const c=data.coverage,line=data.coordinates;
    if(lat<c.south||lat>c.north||lon<c.west||lon>c.east)return {status:'unknown',side:'unknown',message:'Outside boundary-monitor coverage. Border status is unknown.'};
    let lo=0,hi=line.length-1;
    while(lo+1<hi){const mid=(lo+hi)>>1;if(line[mid][0]<=lat)lo=mid;else hi=mid;}
    const a=line[lo],b=line[hi];
    const fraction=Math.max(0,Math.min(1,(lat-a[0])/(b[0]-a[0])));
    const boundaryLon=a[1]+fraction*(b[1]-a[1]);
    const side=lon<boundaryLon?'india':'sri_lanka';
    const sx=111.195,sy=111.195*Math.cos(lat*Math.PI/180);
    let distance=Infinity;
    for(let i=1;i<line.length;i++){
      const ax=(line[i-1][0]-lat)*sx,ay=(line[i-1][1]-lon)*sy;
      const dx=(line[i][0]-line[i-1][0])*sx,dy=(line[i][1]-line[i-1][1])*sy;
      const t=Math.max(0,Math.min(1,-(ax*dx+ay*dy)/(dx*dx+dy*dy)));
      distance=Math.min(distance,Math.hypot(ax+t*dx,ay+t*dy));
    }
    let status,message;
    if(distance<=accuracy/1000+data.planning_buffer_km){status='uncertain';message='At or near the border: GPS accuracy and map uncertainty prevent a reliable side determination.';}
    else if(side==='sri_lanka'){status='outside';message='Border warning: position is on the Sri Lankan side of the mapped maritime boundary.';}
    else if(distance<=data.near_km+accuracy/1000){status='near';message='Approaching maritime border. Stay clear of the boundary.';}
    else{status='inside';message='Position is on the Indian side of the mapped boundary.';}
    return {status,side,distance_km:distance,accuracy_m:accuracy,message};
  }
  function intersects(data,a,b){
    const cross=(a,b,c)=>(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
    return data.coordinates.slice(1).some((d,i)=>{
      const c=data.coordinates[i];
      const overlap=[0,1].every(k=>Math.max(Math.min(a[k],b[k]),Math.min(c[k],d[k]))<=Math.min(Math.max(a[k],b[k]),Math.max(c[k],d[k]))+1e-10);
      return overlap&&cross(a,b,c)*cross(a,b,d)<=1e-18&&cross(c,d,a)*cross(c,d,b)<=1e-18;
    });
  }
  function path(data,points){
    const states=points.map(p=>assess(data,...p));
    const crosses=points.slice(1).some((p,i)=>intersects(data,points[i],p));
    return {crosses_boundary:crosses,has_foreign_side:states.some(s=>s.side==='sri_lanka'),unknown:states.some(s=>s.status==='unknown'),near:states.some(s=>['near','uncertain'].includes(s.status))};
  }
  root.KadalBoundary={assess,intersects,path};
  if(typeof module!=='undefined')module.exports=root.KadalBoundary;
})(globalThis);
