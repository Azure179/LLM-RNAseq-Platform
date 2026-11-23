#!/bin/sh
# Isolated Phase 9.4 harness ONLY. No fake statistical output is ever produced.
case "$*" in
  *deseq2_deg.R*)
    if [ -f /workspace/.phase94-fail-deg ]; then
      echo 'Controlled Phase 9.4 root execution failure' >&2
      exit 24
    fi ;;
  *plot_volcano.R*)
    if [ -f /workspace/.phase94-fail-volcano ]; then
      echo 'Controlled Phase 9.4 renderer failure' >&2
      exit 23
    fi ;;
esac
case "$*" in
  *transform_pca.R*)
    if [ -f /workspace/.phase94-hold-pca ]; then
      /usr/local/bin/Rscript "$@" &
      child=$!
      kill -STOP "$child"
      echo 'Real R child suspended for controlled container interruption' > /evidence/kill-ready
      wait "$child"
      exit $?
    fi ;;
esac
exec /usr/local/bin/Rscript "$@"
