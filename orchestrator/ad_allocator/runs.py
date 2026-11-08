"""Ad Allocator — allocation run persistence (spec §6).

Saves an allocation (engine output) as one aa_allocation_run plus one
aa_allocation_row per group, so recommendations are auditable and comparable
over time (phase-4 backtest).
"""
import json

from sqlalchemy import text


def _actual_share(group, total_spend):
    return (group['spend'] / total_spend) if total_spend else 0.0


def create_run(conn, period, cfg, allocation, created_by=None):
    total_spend = sum(g['spend'] for g in allocation['rows'])
    run_id = conn.execute(text("""
        INSERT INTO aa_allocation_run (period, total_budget, params_json, created_by)
        VALUES (:p, :tb, CAST(:pj AS jsonb), :cb) RETURNING id
    """), {'p': period, 'tb': allocation['total'],
           'pj': json.dumps({'pools': allocation['pools'], 'config': cfg}),
           'cb': created_by}).scalar()

    for r in allocation['rows']:
        conn.execute(text("""
            INSERT INTO aa_allocation_row
              (run_id, product_group_id, rank, actual_share, rec_share, rec_amount,
               gap, action, reason_json, metrics_json)
            VALUES (:run, :g, :rank, :ash, :rsh, :amt, :gap, :act,
                    CAST(:reason AS jsonb), CAST(:metrics AS jsonb))
        """), {
            'run': run_id, 'g': r.get('id'), 'rank': r['rank'],
            'ash': round(_actual_share(r, total_spend), 4),
            'rsh': round(r['rec_share'], 4), 'amt': r['rec_amount'], 'gap': r['gap'],
            'act': r['action'], 'reason': json.dumps({'reason': r['reason']}),
            'metrics': json.dumps({k: r[k] for k in (
                'cpp', 'aov', 'est_profit_7d', 'cover_days', 'cpp_drift',
                'break_even_cpp', 'spend', 'spend_7d') if k in r}),
        })
    conn.commit()
    return run_id


def list_runs(conn, limit=20):
    rows = conn.execute(text("""
        SELECT r.id, r.run_at, r.period, r.total_budget, r.created_by,
               COUNT(row.id) AS group_count
        FROM aa_allocation_run r
        LEFT JOIN aa_allocation_row row ON row.run_id = r.id
        GROUP BY r.id ORDER BY r.run_at DESC LIMIT :lim
    """), {'lim': limit}).fetchall()
    return [dict(r._mapping) for r in rows]


def get_run(conn, run_id):
    run = conn.execute(text("SELECT * FROM aa_allocation_run WHERE id = :id"),
                       {'id': run_id}).fetchone()
    if not run:
        return None
    rows = conn.execute(text("""
        SELECT row.*, g.name AS group_name
        FROM aa_allocation_row row
        LEFT JOIN aa_product_group g ON row.product_group_id = g.id
        WHERE row.run_id = :id ORDER BY row.rank
    """), {'id': run_id}).fetchall()
    return {'run': dict(run._mapping), 'rows': [dict(r._mapping) for r in rows]}
