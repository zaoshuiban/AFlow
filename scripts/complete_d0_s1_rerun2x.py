"""Prospective engineering-only completion amendment; no rerun of completed units."""
import asyncio,json
from pathlib import Path
from datetime import datetime,timezone
import scripts.experiment_d0_s1_rerun2x as m
async def main():
 prior=m.OUT;m.OUT=Path('results/experiment_d0_s1_20261019_rerun2x_completion_v1');out=m.OUT
 if (out/'ledger.json').exists():raise RuntimeError('No retry/resumption')
 rows=m.load(prior/'results.json');ledger=m.load(prior/'ledger.json');protocol=m.load(prior/'protocol.json')
 protocol.update(run_id=out.name,amendment='用户要求完成13题；前5题保留且不重跑，余8题相同模型/生成/工具/预算/评分。单题No Final Submission不再触发全局停止；API、计费、判题基础设施异常停止。工程描述性结果，不构成单一预注册确认实验。',prior_run=str(prior),prior_results_sha256=m.sha(prior/'results.json'),prior_ledger_sha256=m.sha(prior/'ledger.json'),frozen_utc=datetime.now(timezone.utc).isoformat(),driver_sha256=m.sha(__file__),shared_stage_cap_cny=4,prior_calls_included_in_ledger=True)
 m.save(out/'protocol.json',protocol);m.save(out/'results.json',rows);m.save(out/'ledger.json',ledger)
 m.save(out/'protected_before.json',m.load(prior/'protected_before.json'))
 from scripts.async_llm import LLMsConfig
 from openai import AsyncOpenAI
 cfg=LLMsConfig.default().get('deepseek-v4-flash');client=AsyncOpenAI(api_key=cfg.key,base_url=cfg.base_url,max_retries=0)
 public=m.load(m.OLD/'s1_public_tasks.json');backend=m.load(m.OLD/'s1_backend_tasks.json')
 for i,row in enumerate(rows):
  if row['status']!='NOT_RUN':continue
  rows[i]=await m.one(row['id'],client,ledger,public,backend);m.save(out/'results.json',rows)
  print(json.dumps({k:rows[i].get(k) for k in ['id','status','verdict','calls','cost_upper_cny']}),flush=True)
  if rows[i]['status'] in ['API_INFRASTRUCTURE_ERROR','ACCOUNTING_ERROR','ACCOUNTING_BOUND_ERROR'] or rows[i].get('verdict') in ['INFRASTRUCTURE_ERROR','JUDGE_ERROR']:break
 await client.close()
 protected=m.load(out/'protected_before.json');m.save(out/'protected_after.json',{'checked':len(protected),'changed':[p for p,h in protected.items() if not Path(p).exists() or m.sha(p)!=h]})
if __name__=='__main__':asyncio.run(main())
