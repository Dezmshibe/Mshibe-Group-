-- Mshibe Group: lock the database down.
-- Run AFTER 01_schema.sql.
--
-- Supabase automatically exposes every table in the "public" schema through a public
-- REST API that anyone holding the (public) anon key can call. Your Flask app does NOT
-- use that API: it connects straight to Postgres with the database password. So we
-- switch Row Level Security on for every table and add no policies, which means the
-- public API can read and write nothing. Your Flask app is unaffected (the postgres
-- role bypasses RLS).
--
-- Most important for site_settings (holds the admin password hash) and for orders,
-- appointments and contact_messages (customers' names, emails, phones, addresses).

alter table public.site_settings    enable row level security;
alter table public.menu_categories  enable row level security;
alter table public.menu_items       enable row level security;
alter table public.orders           enable row level security;
alter table public.order_items      enable row level security;
alter table public.services         enable row level security;
alter table public.appointments     enable row level security;
alter table public.contact_messages enable row level security;

-- Belt and braces: also remove the API roles' table privileges.
revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
alter default privileges in schema public revoke all on tables    from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;

-- ----------------------------------------------------------------------------
-- OPTIONAL: only if you later build a front-end that talks to Supabase directly
-- with the anon key. Read-only public menu and services (nothing else):
--
-- grant select on public.menu_categories, public.menu_items, public.services to anon;
-- create policy "public can read categories" on public.menu_categories for select to anon using (true);
-- create policy "public can read available items" on public.menu_items for select to anon using (available);
-- create policy "public can read active services" on public.services for select to anon using (active);
-- ----------------------------------------------------------------------------
