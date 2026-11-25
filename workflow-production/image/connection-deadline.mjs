// Bound connection setup without treating a quiet, established response as a failure.
export function armConnectionDeadline(request, deadline, error){
 const timer=setTimeout(()=>request.destroy(error),deadline);
 timer.unref?.();
 const clear=()=>clearTimeout(timer);
 request.once('socket',socket=>{
  if(socket.connecting)socket.once('connect',clear);
  else clear();
 });
 request.once('response',clear);
 request.once('close',clear);
}
