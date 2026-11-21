\ir bootstrap.sql

CREATE OR REPLACE FUNCTION lab5_craft.stage(data jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    item record;
    job uuid := (data->>'job_id')::uuid;
    actor text := ((data->>'player_id')::uuid)::text;
    lobby text := ((data->>'lobby_id')::uuid)::text;
BEGIN
    IF jsonb_array_length(data->'inputs') NOT BETWEEN 1 AND 20
       OR data->'inputs' IS NULL THEN
        RAISE EXCEPTION 'Invalid material list' USING ERRCODE='22023';
    END IF;
    INSERT INTO lab5_craft.participation(job_id) VALUES(job);
    FOR item IN
        SELECT item_id, sum(count)::integer AS amount,
               bool_and(coalesce(count BETWEEN 1 AND 1000, false)) AS valid
        FROM jsonb_to_recordset(data->'inputs') AS input(item_id text, count integer)
        GROUP BY item_id ORDER BY item_id
    LOOP
        IF item.item_id IS NULL OR item.item_id !~ '^[a-zA-Z0-9_-]{1,80}$'
           OR item.valid IS DISTINCT FROM true OR item.amount IS NULL THEN
            RAISE EXCEPTION 'Invalid material' USING ERRCODE='22023';
        END IF;
        UPDATE public.player_resources AS stock SET amount=stock.amount-item.amount
        WHERE stock.player_id=actor AND stock.lobby_id=lobby
          AND stock.item_id=item.item_id AND stock.amount>=item.amount;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Insufficient resources' USING ERRCODE='P5001';
        END IF;
    END LOOP;
    INSERT INTO public.transactions(transaction_id,idempotency_key,type,status,player_id,lobby_id,items,reason,reference_id)
    VALUES(job,'lab5-2pc:'||job,'consume','completed',actor,lobby,data->'inputs','crafting.2pc',job::text);
END;
$$;
REVOKE ALL ON FUNCTION lab5_craft.stage(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION lab5_craft.stage(jsonb) TO pad_craft_participant;
COMMIT;
