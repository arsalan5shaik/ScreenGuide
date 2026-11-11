-- ScreenGuide cloud sync — initial schema
--
-- Run this once in the Supabase SQL editor (Dashboard → SQL Editor → New query).
-- Safe to re-run: every statement is idempotent.
--
-- Design notes
--   * The desktop app's SQLite journal stays the source of truth offline. This
--     table is a mirror keyed by (user_id, client_id), where client_id is a
--     UUID minted on the client. Server-side ids are never sent back as the
--     identity, so a row survives a local DB rebuild.
--   * Deletes are tombstones. A hard delete can't propagate — the other device
--     would simply re-upload the row on its next push.
--   * updated_at drives last-write-wins merging and is always set server-side,
--     so a device with a wrong clock can't pin a row into the future.

create extension if not exists "pgcrypto";

-- ── profiles ──────────────────────────────────────────────────────────────
-- One row per auth user. Exists so the app has somewhere to hang per-account
-- data that isn't a journal entry or a setting.

create table if not exists public.profiles (
    id          uuid primary key references auth.users (id) on delete cascade,
    email       text,
    display_name text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- ── journal_entries ───────────────────────────────────────────────────────

create table if not exists public.journal_entries (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references auth.users (id) on delete cascade,
    -- Stable identity minted by the client; the real primary key for sync.
    client_id     text not null,
    question      text not null,
    answer        text not null,
    app_key       text default '',
    window_title  text default '',
    provider      text default '',
    model         text default '',
    -- When the exchange happened on the device, not when it reached us.
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    deleted_at    timestamptz,
    unique (user_id, client_id)
);

-- Sync pulls "everything changed since X", so this is the hot path.
create index if not exists journal_entries_user_updated_idx
    on public.journal_entries (user_id, updated_at desc);

-- ── user_settings ─────────────────────────────────────────────────────────
-- Key/value so new settings don't need a migration to sync.

create table if not exists public.user_settings (
    user_id     uuid not null references auth.users (id) on delete cascade,
    key         text not null,
    value       jsonb not null default 'null'::jsonb,
    updated_at  timestamptz not null default now(),
    primary key (user_id, key)
);

create index if not exists user_settings_user_updated_idx
    on public.user_settings (user_id, updated_at desc);

-- ── updated_at maintenance ────────────────────────────────────────────────
-- Set server-side so a client with a skewed clock can't write a timestamp
-- that hides every later edit from the merge.

create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists journal_entries_touch on public.journal_entries;
create trigger journal_entries_touch
    before insert or update on public.journal_entries
    for each row execute function public.touch_updated_at();

drop trigger if exists user_settings_touch on public.user_settings;
create trigger user_settings_touch
    before insert or update on public.user_settings
    for each row execute function public.touch_updated_at();

drop trigger if exists profiles_touch on public.profiles;
create trigger profiles_touch
    before update on public.profiles
    for each row execute function public.touch_updated_at();

-- ── new users get a profile automatically ─────────────────────────────────

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
    insert into public.profiles (id, email)
    values (new.id, new.email)
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- ── Row Level Security ────────────────────────────────────────────────────
-- The API server uses the service role key, which bypasses RLS, and scopes
-- every query by user_id itself. These policies are the second line of
-- defence: they make the anon key safe to ship in the desktop app, because
-- even with it in hand a user can only ever reach their own rows.

alter table public.profiles        enable row level security;
alter table public.journal_entries enable row level security;
alter table public.user_settings   enable row level security;

drop policy if exists "own profile" on public.profiles;
create policy "own profile" on public.profiles
    for all to authenticated
    using (auth.uid() = id)
    with check (auth.uid() = id);

drop policy if exists "own journal" on public.journal_entries;
create policy "own journal" on public.journal_entries
    for all to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists "own settings" on public.user_settings;
create policy "own settings" on public.user_settings
    for all to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);
