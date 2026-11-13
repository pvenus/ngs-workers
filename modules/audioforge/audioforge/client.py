from __future__ import annotations
import httpx
from .config import API_URL

class AudioForgeClient:
    def __init__(self,base_url:str=API_URL):self.base_url=base_url
    def _request(self,method,path,**kwargs):
        with httpx.Client(base_url=self.base_url,timeout=10) as client:
            response=client.request(method,path,**kwargs)
            if response.is_error:raise RuntimeError(f"AudioForge API {response.status_code}: {response.text}")
            return response.json()
    def health(self):return self._request("GET","/health")
    def profiles(self):return self._request("GET","/v1/profiles")
    def submit(self,payload,key=None):return self._request("POST","/v1/jobs",json=payload,headers={"Idempotency-Key":key} if key else {})
    def status(self,job_id):return self._request("GET",f"/v1/jobs/{job_id}")
    def cancel(self,job_id):return self._request("DELETE",f"/v1/jobs/{job_id}")
