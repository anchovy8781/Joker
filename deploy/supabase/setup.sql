-- YouTube Exchange — Supabase scheduler setup.
-- Run once in Supabase Dashboard → SQL Editor, AFTER the Render service is live.
-- Replace the two placeholders below:
--   <APP_URL>      your Render address, e.g. https://youtube-exchange.onrender.com
--   <TICK_SECRET>  the YEX_TICK_SECRET value from Render → Environment
--
-- Every minute pg_cron calls the app's /api/internal/tick (via pg_net), which runs the
-- scheduled jobs (data collection, daily price update, reservation activation at market
-- open, auto orders, risk scan, reconciliation) and keeps the free web service awake.

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- Re-running this file replaces the job instead of adding a duplicate.
select cron.unschedule(jobid) from cron.job where jobname = 'ytx-tick';

select cron.schedule(
  'ytx-tick',
  '* * * * *',
  $$
  select net.http_post(
    url := '<APP_URL>/api/internal/tick',
    headers := jsonb_build_object('Content-Type', 'application/json', 'X-Tick-Secret', '<TICK_SECRET>'),
    body := '{}'::jsonb,
    timeout_milliseconds := 55000
  );
  $$
);

-- Check it works (a few minutes after running this file):
--   select status_code, created from net._http_response order by created desc limit 5;   -- expect 200
--   select * from cron.job_run_details order by start_time desc limit 5;
