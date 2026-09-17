FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY config.py guardrails.py ingestion.py retriever.py checkpointer.py graph.py lambda_handler.py online_eval_handler.py slo_monitor_handler.py alarm_forwarder_handler.py ${LAMBDA_TASK_ROOT}
COPY evals ${LAMBDA_TASK_ROOT}/evals

CMD ["lambda_handler.handler"]
