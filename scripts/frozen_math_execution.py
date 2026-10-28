"""Execute the same frozen generated source for each input in a bounded worker."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from scripts.frozen_math_worker import validate
from scripts.offline_loop_protocol import digest


async def run_frozen_math(code, problem, timeout=6):
    trace={'code':code,'code_sha256':digest(code),'problem_sha256':digest(problem),'worker_wall_limit_seconds':timeout}
    try: validate(code)
    except (ValueError,SyntaxError) as exc:
        return {'status':'error','response':'','observation':str(exc),'tool_trace':trace}
    with tempfile.TemporaryDirectory(prefix='aflow-candidate-') as cwd:
        process=await asyncio.create_subprocess_exec(sys.executable,'-I',str(Path(__file__).with_name('frozen_math_worker.py')),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            cwd=cwd,env={'PATH':os.defpath,'LANG':'C.UTF-8'})
        try:
            stdout,stderr=await asyncio.wait_for(process.communicate(json.dumps({'code':code,'problem':problem}).encode()),timeout)
        except (asyncio.TimeoutError,asyncio.CancelledError) as exc:
            if process.returncode is None:process.kill()
            await process.wait()
            if isinstance(exc,asyncio.CancelledError):raise
            return {'status':'error','response':'','observation':'candidate_worker_timeout','tool_trace':trace}
    trace.update(exit_code=process.returncode,stdout=stdout.decode(errors='replace')[:4000],stderr=stderr.decode(errors='replace')[:1000])
    try:
        if process.returncode:raise ValueError('candidate_worker_exit_'+str(process.returncode))
        result=json.loads(stdout)
        if result['status']=='success':
            return {'status':'success','response':'\\boxed{'+str(result['value'])+'}','tool_trace':trace}
        return {'status':result['status'],'response':'','observation':result.get('error','outside_scope'),'tool_trace':trace}
    except (ValueError,KeyError) as exc:
        return {'status':'error','response':'','observation':str(exc),'tool_trace':trace}
