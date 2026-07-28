#!/bin/zsh
# A/B 基准评测编排脚本：LongMemEval 100 题 + LoCoMo 200 题 × {default, local(BGE)}
#
# 用法:
#   nohup ./scripts/run_ab_benchmarks.sh > /tmp/ams_eval/orchestrator_stdout.log 2>&1 &
#
# 说明:
#   - 每轮在独立的临时 CWD 下运行（SQLite/Chroma 均为 CWD 相对路径，天然隔离）
#   - default 两轮并行 -> 完成后 local 两轮并行（避免 4 进程争抢 CPU/API）
#   - 结果 JSON 落盘 backend/results/，全部完成后创建 /tmp/ams_eval/ALL_DONE
set -u
BACKEND="$(cd "$(dirname "$0")/.." && pwd)"
PY=$BACKEND/.venv/bin/python
WORK=/tmp/ams_eval
LOG=$WORK/orchestrator2.log
mkdir -p $WORK
rm -f $WORK/ALL_DONE
echo "=== run_ab_benchmarks start $(date) backend=$BACKEND ===" >> $LOG

run_lme() {  # $1=工作目录名 $2=user_id $3=输出名 $4=日志名（额外环境由调用方 export）
  mkdir -p $WORK/$1 && cd $WORK/$1
  PYTHONPATH=$BACKEND REDIS_URL=fakeredis:// ANONYMIZED_TELEMETRY=False \
    $PY -m app.benchmarks.stratified_eval \
    --data $BACKEND/app/benchmarks/data/longmemeval/longmemeval_s \
    --per-class 20 --user-id $2 \
    --output $BACKEND/results/$3 > $WORK/$4 2>&1
}

run_locomo() {  # $1=工作目录名 $2=user_id $3=输出名 $4=日志名
  mkdir -p $WORK/$1 && cd $WORK/$1
  PYTHONPATH=$BACKEND REDIS_URL=fakeredis:// ANONYMIZED_TELEMETRY=False \
    $PY -m app.benchmarks.runner run --suite locomo \
    --data $BACKEND/app/benchmarks/data/locomo \
    --limit 200 --user-id $2 \
    --output $BACKEND/results/$3 > $WORK/$4 2>&1
}

# ---- Round 1: default（Chroma 内置 all-MiniLM-L6-v2）----
run_lme r2_lme_default 9910 lme100_default.json lme100_default2.log &
P1=$!
run_locomo r2_locomo_default 9911 locomo200_default.json locomo200_default2.log &
P2=$!
echo "default rounds: lme=$P1 locomo=$P2 $(date)" >> $LOG
wait $P1; echo "lme default exit=$? $(date)" >> $LOG
wait $P2; echo "locomo default exit=$? $(date)" >> $LOG

# ---- Round 2: local（sentence-transformers BGE，英文数据集用 bge-small-en-v1.5）----
export EMBEDDING_PROVIDER=local
export EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
export HF_ENDPOINT=https://hf-mirror.com
run_lme r2_lme_local 9912 lme100_local.json lme100_local2.log &
P3=$!
run_locomo r2_locomo_local 9913 locomo200_local.json locomo200_local2.log &
P4=$!
echo "local rounds: lme=$P3 locomo=$P4 $(date)" >> $LOG
wait $P3; echo "lme local exit=$? $(date)" >> $LOG
wait $P4; echo "locomo local exit=$? $(date)" >> $LOG

echo "ALL DONE $(date)" >> $LOG
touch $WORK/ALL_DONE
