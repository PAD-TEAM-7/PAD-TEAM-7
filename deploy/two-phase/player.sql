\ir bootstrap.sql

CREATE OR REPLACE FUNCTION lab5_craft.stage(data jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    job uuid := (data->>'job_id')::uuid;
    actor uuid := (data->>'player_id')::uuid;
    item text := data->'output'->>'item_id';
    quantity integer := (data->'output'->>'count')::integer;
    new_version integer;
BEGIN
    IF item IS NULL OR item !~ '^[a-zA-Z0-9_-]{1,80}$'
       OR quantity IS NULL OR quantity NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'Invalid inventory grant' USING ERRCODE='22023';
    END IF;
    INSERT INTO lab5_craft.participation(job_id) VALUES(job);
    UPDATE public.players SET inventory_version=inventory_version+1
    WHERE player_id=actor RETURNING inventory_version INTO new_version;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Player does not exist' USING ERRCODE='P5002';
    END IF;
    INSERT INTO public.inventory_items(player_id,item_id,count) VALUES(actor,item,quantity)
    ON CONFLICT (player_id,item_id) DO UPDATE SET count=public.inventory_items.count+EXCLUDED.count;
    INSERT INTO public.inventory_events(idempotency_key,player_id,payload,response,inventory_version)
    VALUES('lab5-2pc:'||job,actor,data::text,jsonb_build_array(data->'output'),new_version);
END;
$$;
REVOKE ALL ON FUNCTION lab5_craft.stage(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION lab5_craft.stage(jsonb) TO pad_craft_participant;
COMMIT;
