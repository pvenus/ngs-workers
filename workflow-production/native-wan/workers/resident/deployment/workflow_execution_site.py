"""Real HTTP-only workflow site factory; credentials/config are operator supplied.

The Request gateway authenticates this dedicated executor identity and proves
exclusive parent ownership. No user-session impersonation or Common DB access.
"""
from pathlib import Path
import stat
from workers.resident.adapters.workflow_gateway_http import HttpWorkflowGateway
from workers.resident.deployment.workflow_execution import WorkflowExecutor, OfficialWorkflowPorts


def build_workflow_host(config):
    root=Path(config['stateRoot'])
    token_path=Path(config['requestTokenFile'])
    if (token_path.is_symlink()or not token_path.is_file()or not token_path.is_absolute()
        or stat.S_IMODE(token_path.stat().st_mode)!=0o600):
        raise ValueError('WORKFLOW_SERVICE_TOKEN_FILE_INVALID')
    token=token_path.read_text().strip()
    if len(token)<32:raise ValueError('WORKFLOW_SERVICE_TOKEN_INVALID')
    gateway=HttpWorkflowGateway(config['requestUrl'],token)
    official=OfficialWorkflowPorts(gateway)
    pins={}
    def authorize(rid,request,plan):
        binding=official.authorize(rid,request,plan)
        if rid in pins and pins[rid]!=binding:raise ValueError('WORKFLOW_SITE_PARENT_CHANGED')
        pins[rid]=binding
        return binding
    def owned(rid):
        binding=pins.get(rid)
        if binding is None:return False
        parent=gateway.read(binding)
        return (parent.get('requestId')==rid and parent.get('requestHash')==binding['requestHash']
                and parent.get('planHash')==binding['definitionHash'])
    def preflight():
        # The authenticated API validates executor and original ownership. An
        # empty list is a valid ready service, not permission to invent a parent.
        gateway.runs()
        return True
    engine=WorkflowExecutor(root/'workflow-runs.sqlite',official,
                            exclusive_owner=owned,authorize_run=authorize)
    return {'engine':engine,'pending_runs':official.pending_runs,'preflight':preflight}
