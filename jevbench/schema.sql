-- Works on plain Postgres; on Tiger Data / TimescaleDB it also makes `events`
-- a hypertable with a per-minute continuous aggregate of call latency.

create table if not exists runs (
  run_id   text primary key,
  started  timestamptz,
  finished timestamptz,
  planned  int,
  state    text,
  config   jsonb
);

create table if not exists episodes (
  episode_id text primary key,
  run_id text, ts timestamptz,
  bench text, grp text, task_id text, llm text, decision text, surface text, rep int, n_tools int,
  status text, score double precision, success boolean,
  ttftc_s double precision, ttfctc_s double precision,
  agent_time_s double precision, wall_time_s double precision,
  decision_time_s double precision, llm_time_s double precision, first_byte_s double precision,
  n_decision_calls int, n_llm_calls int, n_tool_calls int, calls_before_correct int,
  wrong_calls int, distractor_calls int, hatch_used boolean,
  shortlist_recall double precision, avg_shortlist_size double precision,
  input_tokens bigint, output_tokens bigint, cached_tokens bigint, cost_usd double precision,
  error text, details jsonb
);
create index if not exists episodes_run on episodes(run_id);

create table if not exists events (
  ts timestamptz not null,
  run_id text, episode_id text, kind text, provider text,
  latency_s double precision, data jsonb
);
create index if not exists events_run on events(run_id, ts);

do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'timescaledb') then
    create extension if not exists timescaledb;
    perform create_hypertable('events', 'ts', if_not_exists => true, migrate_data => true);
    if not exists (select 1 from pg_class where relname = 'call_latency_1m') then
      execute $v$
        create materialized view call_latency_1m
        with (timescaledb.continuous) as
        select time_bucket('1 minute', ts) as bucket, run_id, kind, provider,
               count(*) as n, avg(latency_s) as avg_s, max(latency_s) as max_s
        from events
        where kind in ('decision', 'llm', 'probe')
        group by bucket, run_id, kind, provider
        with no data
      $v$;
      perform add_continuous_aggregate_policy('call_latency_1m',
        start_offset => interval '1 day', end_offset => interval '1 minute',
        schedule_interval => interval '1 minute');
    end if;
  end if;
end $$;

-- show the newest minute immediately (real-time aggregation), not only after the refresh job
do $$
begin
  if exists (select 1 from pg_class where relname = 'call_latency_1m') then
    alter materialized view call_latency_1m set (timescaledb.materialized_only = false);
  end if;
end $$;
