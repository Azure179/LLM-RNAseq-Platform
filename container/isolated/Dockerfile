# Trusted local derivative; base identity checked by validation harness before build.
FROM bulk-rnaseq:phase9-5
COPY container/isolated/worker.py /opt/rnaseq/isolated_worker.py
HEALTHCHECK NONE
USER 10001:10001
ENTRYPOINT ["python", "-I", "/opt/rnaseq/isolated_worker.py"]
CMD []
