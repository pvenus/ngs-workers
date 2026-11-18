import functools,json
QUERY_CAP=512

def wrap_attention(original, logger=None):
 seen=set()
 @functools.wraps(original)
 def bounded(query,key_t,value,query_chunk_size=1024,kv_chunk_size=None,kv_chunk_size_min=None,use_checkpoint=True,upcast_attention=False,mask=None):
  applied=min(query_chunk_size,QUERY_CAP)
  record={'query_shape':list(query.shape),'key_shape':list(key_t.shape),'value_shape':list(value.shape),'dtype':str(query.dtype),'original_query_chunk':query_chunk_size,'applied_query_chunk':applied,'kv_chunk_size':kv_chunk_size,'kv_chunk_size_min':kv_chunk_size_min,'upcast_attention':upcast_attention,'use_checkpoint':use_checkpoint,'mask_shape':None if mask is None else list(mask.shape)}
  token=json.dumps(record,sort_keys=True)
  if logger is not None and token not in seen:seen.add(token);logger(record)
  return original(query,key_t,value,query_chunk_size=applied,kv_chunk_size=kv_chunk_size,kv_chunk_size_min=kv_chunk_size_min,use_checkpoint=use_checkpoint,upcast_attention=upcast_attention,mask=mask)
 return bounded
