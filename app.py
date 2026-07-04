from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from datetime import date, datetime, timedelta

import garmin_client as gc
import excel_handler as eh
import notes as nt
import plan_store as ps

app = Flask(__name__)
app.secret_key = 'dashboard_key_2026'


# ── template filters ───────────────────────────────────────────────────────────

@app.template_filter('fmt_num')
def fmt_num(val):
    if val is None:
        return '-'
    try:
        v = float(val)
        return f"{v:+.2f}" if v != 0 else "0.00"
    except (ValueError, TypeError):
        return str(val)


@app.template_filter('fmt_date')
def fmt_date(val):
    if val is None:
        return ''
    if isinstance(val, datetime):
        return val.strftime('%d/%m/%Y')
    return str(val)


@app.template_filter('color_num')
def color_num(val):
    try:
        v = float(val)
        if v > 0:
            return 'text-success'
        elif v < 0:
            return 'text-danger'
        return ''
    except (ValueError, TypeError):
        return ''


@app.template_filter('cuenta_label')
def cuenta_label(val):
    return eh.CUENTA_LABELS.get(val, val)


@app.template_filter('cuentas_apuesta_label')
def cuentas_apuesta_label(val):
    return eh.CUENTAS_APUESTA_LABELS.get(val, val)


@app.template_filter('cuentas_apuesta_option_label')
def cuentas_apuesta_option_label(val):
    return eh.CUENTAS_APUESTA_OPTION_LABELS.get(val, val)


# ── finanzas helper ────────────────────────────────────────────────────────────

def _fin():
    yo_init, asenjo_init = eh.load_initial_balances()
    movimientos = eh.load_movimientos()
    apuestas = eh.load_apuestas()
    saldos = eh.compute_saldos(movimientos, apuestas, yo_init, asenjo_init)
    return movimientos, apuestas, saldos


@app.template_filter('plan_type_label')
def plan_type_label(val):
    return ps.TYPE_LABELS.get(val, val)


# ── plan helper ────────────────────────────────────────────────────────────────

def _load_plan_weeks():
    """Agrupa las actividades en semanas y marca cada una como hecha según lo que
    realmente se ha registrado en Garmin (comparando por fecha y tipo de actividad).
    Los km mostrados pasan a ser los reales de la actividad registrada, no el objetivo."""
    weeks = ps.build_weeks(ps.load_activities())
    today_s = date.today().isoformat()
    dates = [a['date'] for w in weeks for a in w['activities'] if a['date'] <= today_s]

    running_acts  = gc.activities_range(min(dates), max(dates), 'running')  if dates else []
    strength_acts = gc.activities_range(min(dates), max(dates), 'strength') if dates else []
    bike_acts     = gc.activities_range(min(dates), max(dates), 'bike')     if dates else []
    running_by_date  = {a['date']: a for a in running_acts}
    strength_by_date = {a['date']: a for a in strength_acts}
    bike_by_date     = {a['date']: a for a in bike_acts}

    for w in weeks:
        done_count = 0
        for a in w['activities']:
            match = None
            if a['date'] and a['date'] <= today_s:
                if a['type'] == 'gym':
                    match = strength_by_date.get(a['date'])
                elif a['type'] == 'bike':
                    match = bike_by_date.get(a['date'])
                elif a['type'] == 'run':
                    match = running_by_date.get(a['date'])
            a['done'] = match is not None
            a['actual_km'] = match['distance_km'] if match else None
            a['display_km'] = a['actual_km'] if a['done'] else a['target_km']
            if a['done']:
                done_count += 1
        w['done_count']  = done_count
        w['total_count'] = len(w['activities'])
        w['pct_done']    = round(done_count / len(w['activities']) * 100) if w['activities'] else 0
        w['run_km']      = round(sum(a['display_km'] or 0 for a in w['activities'] if a['type'] == 'run'), 1)
    return weeks


# ── HOME ───────────────────────────────────────────────────────────────────────

@app.route('/')
def home():
    weeks         = _load_plan_weeks()
    today_sessions = [a for a in ps.today_activities(weeks) if a['type'] != 'rest']
    upcoming      = ps.upcoming_activities(weeks, 3)
    summary       = gc.daily_summary()
    movimientos, apuestas, _saldos = _fin()
    pending_movs = [m for m in movimientos if m['estado'] == 'Pendiente']
    pending_bets = [a for a in apuestas if a['pendiente']]
    pending_bets_open = sorted(
        (a for a in pending_bets if a['fecha'] and a['fecha'].date() <= date.today()),
        key=lambda a: a['fecha'],
    )

    now = datetime.now()
    mes_actual_str = f"{eh.MONTH_NAMES[now.month - 1]}-{str(now.year)[2:]}"
    historial = eh.compute_historial(movimientos, apuestas)
    fila_mes_actual = next((r for r in historial if r['mes'] == mes_actual_str), None)
    ganado_mes_total = fila_mes_actual['bal_total'] if fila_mes_actual else 0.0
    ganado_mes_apuestas = fila_mes_actual['bal_apuestas'] if fila_mes_actual else 0.0

    return render_template('home.html',
        today=date.today(), today_sessions=today_sessions, upcoming=upcoming,
        summary=summary, pending_movs=pending_movs, pending_bets_count=len(pending_bets),
        pending_bets_open=pending_bets_open,
        ganado_mes_total=ganado_mes_total, ganado_mes_apuestas=ganado_mes_apuestas,
        notes=nt.load_notes(),
    )


@app.route('/notes/add', methods=['POST'])
def add_note():
    nt.add_note(request.form.get('text', ''))
    return redirect(url_for('home'))


@app.route('/notes/<int:note_id>/toggle', methods=['POST'])
def toggle_note(note_id):
    nt.toggle_note(note_id)
    return redirect(url_for('home'))


@app.route('/notes/<int:note_id>/delete', methods=['POST'])
def delete_note(note_id):
    nt.delete_note(note_id)
    return redirect(url_for('home'))


# ── RUNNING ────────────────────────────────────────────────────────────────────

@app.route('/running')
def running():
    weeks          = _load_plan_weeks()
    cw             = ps.current_week(weeks)
    today_sessions = [a for a in ps.today_activities(weeks) if a['type'] != 'rest']
    upcoming       = ps.upcoming_activities(weeks, 4)
    summary        = gc.daily_summary()
    steps7         = gc.steps_last_n(7)
    acts           = gc.recent_activities(5)
    return render_template('running.html',
        today=date.today(), today_sessions=today_sessions, upcoming=upcoming,
        current_week=cw, summary=summary, steps7=steps7, recent_acts=acts,
    )


@app.route('/plan')
def plan():
    weeks  = _load_plan_weeks()
    cw     = ps.current_week(weeks)
    cw_num = cw['num'] if cw else 1
    return render_template('plan.html',
        weeks=weeks, current_week_num=cw_num, today=date.today(),
        types=ps.TYPES, type_labels=ps.TYPE_LABELS,
    )


@app.route('/plan/week/<start_date>/title', methods=['POST'])
def set_plan_week_title(start_date):
    ps.set_week_title(start_date, request.form.get('title', ''))
    return redirect(url_for('plan'))


@app.route('/plan/add', methods=['POST'])
def add_plan_activity():
    try:
        date_str = request.form['date']
        desc     = request.form.get('desc', '').strip()
        type_    = request.form.get('type', 'other')
        km_str   = request.form.get('target_km', '').strip()
        target_km = float(km_str) if km_str else None
        ps.add_activity(date_str, desc, type_, target_km)
        flash('Actividad añadida.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('plan'))


@app.route('/plan/<int:activity_id>/edit', methods=['POST'])
def edit_plan_activity(activity_id):
    try:
        date_str = request.form['date']
        desc     = request.form.get('desc', '').strip()
        type_    = request.form.get('type', 'other')
        km_str   = request.form.get('target_km', '').strip()
        target_km = float(km_str) if km_str else None
        ps.update_activity(activity_id, date_str, desc, type_, target_km)
        flash('Actividad actualizada.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('plan'))


@app.route('/plan/<int:activity_id>/delete', methods=['POST'])
def delete_plan_activity(activity_id):
    ps.delete_activity(activity_id)
    flash('Actividad eliminada.', 'success')
    return redirect(url_for('plan'))


@app.route('/activities')
def activities():
    end    = date.today()
    start  = end - timedelta(days=90)
    acts_running  = gc.activities_range(start, end, 'running')
    acts_strength = gc.activities_range(start, end, 'strength')
    acts_bike     = gc.activities_range(start, end, 'bike')
    pesas_labels  = gc.get_pesas_labels()
    for a in acts_strength:
        a['label'] = pesas_labels.get(str(a['id']), '')
    weekly = gc.weekly_volume(12)
    prs       = gc.personal_records()
    predicted = gc.race_predictions()
    return render_template('activities.html',
        activities=acts_running, acts_strength=acts_strength, acts_bike=acts_bike,
        pesas_label_options=gc.PESAS_LABELS,
        weekly=weekly, prs=prs, predicted=predicted, today=date.today())


@app.route('/activities/pesas/<int:activity_id>/label', methods=['POST'])
def update_pesas_label(activity_id):
    label = request.form.get('label', '')
    if label in gc.PESAS_LABELS:
        gc.set_pesas_label(activity_id, label)
    return redirect(url_for('activities'))


@app.route('/activities/<int:activity_id>')
def activity_detail(activity_id):
    act    = gc.activity_summary(activity_id)
    splits = gc.activity_splits(activity_id)
    prs    = gc.personal_records()
    is_pr  = any(pr and pr.get('activity_id') == activity_id for pr in prs.values())
    return render_template('activity_detail.html',
        activity_id=activity_id, act=act, splits=splits, is_pr=is_pr, today=date.today())


@app.route('/api/activity/<int:activity_id>/series')
def api_activity_series(activity_id):
    return jsonify(gc.activity_series(activity_id))


@app.route('/stats')
def stats():
    summary   = gc.daily_summary()
    s_streak  = gc.step_streak()
    f_streak  = gc.floors_streak()
    rhr       = gc.resting_hr_history(30)
    max_hr    = gc.max_hr_from_activities()
    zones     = gc.hr_zones()
    zone_time = gc.zone_time_recent(30)
    vo2       = gc.vo2max_history(90)
    yearly    = gc.yearly_stats()
    return render_template('stats.html',
        summary=summary, step_streak=s_streak, floors_streak=f_streak,
        rhr_history=rhr, max_hr=max_hr, hr_zones=zones, zone_time=zone_time,
        vo2max=vo2, yearly=yearly, today=date.today(),
    )


# ── FINANZAS ───────────────────────────────────────────────────────────────────

@app.route('/finanzas')
def finanzas():
    movimientos, apuestas, saldos = _fin()
    recent_movs  = sorted(movimientos, key=lambda m: m['mes'] or datetime.min, reverse=True)[:10]
    pending_movs = [m for m in movimientos if m['estado'] == 'Pendiente']
    pending_bets = [a for a in apuestas if a['pendiente']]
    return render_template('finanzas.html',
        saldos=saldos, bookmakers=eh.BOOKMAKERS,
        recent_movs=recent_movs, pending_movs=pending_movs,
        pending_bets_count=len(pending_bets),
    )


@app.route('/movimientos')
def movimientos():
    movimientos_list = eh.load_movimientos()
    mes_filter    = request.args.get('mes', '')
    tipo_filter   = request.args.get('tipo', '')
    cuenta_filter = request.args.get('cuenta', '')
    estado_filter = request.args.get('estado', '')

    filtered = movimientos_list
    if mes_filter:    filtered = [m for m in filtered if m['mes_str'] == mes_filter]
    if tipo_filter:   filtered = [m for m in filtered if m['tipo'] == tipo_filter]
    if cuenta_filter: filtered = [m for m in filtered if m['cuenta'] == cuenta_filter]
    if estado_filter: filtered = [m for m in filtered if m['estado'] == estado_filter]

    filtered = sorted(filtered, key=lambda m: m['mes'] or datetime.min, reverse=True)
    available_months = sorted({m['mes_str'] for m in movimientos_list if m['mes_str']}, reverse=True)

    return render_template('movimientos.html',
        movimientos=filtered, available_months=available_months,
        tipos=eh.TIPOS_MOV, cuentas=eh.CUENTAS, bookmakers=eh.BOOKMAKERS,
        mes_filter=mes_filter, tipo_filter=tipo_filter,
        cuenta_filter=cuenta_filter, estado_filter=estado_filter,
    )


@app.route('/movimientos/add', methods=['POST'])
def add_movimiento():
    try:
        fecha_str = request.form.get('fecha', '').strip()
        mes_str   = request.form.get('mes', '').strip()
        tipo      = request.form['tipo']
        cuenta    = request.form['cuenta']
        casa      = request.form.get('casa', '').strip()
        cantidad  = float(request.form['cantidad'])
        estado    = 'Recibido' if tipo in eh.TIPOS_MOV_SIMPLES else request.form['estado']
        if tipo in eh.TIPOS_MOV_SIMPLES:
            cuenta, casa = '', ''

        fecha = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else None
        if mes_str:
            mes_dt = datetime.strptime(mes_str + '-01', '%Y-%m-%d')
        elif fecha:
            mes_dt = datetime(fecha.year, fecha.month, 1)
        else:
            mes_dt = datetime(datetime.now().year, datetime.now().month, 1)

        eh.add_movimiento(fecha, mes_dt, tipo, cuenta, casa, cantidad, estado)
        flash('Movimiento añadido.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('movimientos'))


@app.route('/movimientos/<int:row>/edit', methods=['POST'])
def edit_movimiento(row):
    try:
        fecha_str = request.form.get('fecha', '').strip()
        mes_str   = request.form.get('mes', '').strip()
        tipo      = request.form['tipo']
        cuenta    = request.form['cuenta']
        casa      = request.form.get('casa', '').strip()
        cantidad  = float(request.form['cantidad'])
        estado    = 'Recibido' if tipo in eh.TIPOS_MOV_SIMPLES else request.form['estado']
        if tipo in eh.TIPOS_MOV_SIMPLES:
            cuenta, casa = '', ''

        fecha = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else None
        if mes_str:
            mes_dt = datetime.strptime(mes_str + '-01', '%Y-%m-%d')
        elif fecha:
            mes_dt = datetime(fecha.year, fecha.month, 1)
        else:
            mes_dt = datetime(datetime.now().year, datetime.now().month, 1)

        eh.update_movimiento(row, fecha, mes_dt, tipo, cuenta, casa, cantidad, estado)
        flash('Movimiento actualizado.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('movimientos'))


@app.route('/movimientos/<int:row>/estado', methods=['POST'])
def toggle_estado(row):
    nuevo = request.form.get('estado')
    if nuevo in ('Pendiente', 'Recibido'):
        eh.update_movimiento_estado(row, nuevo)
        flash(f'Estado → {nuevo}.', 'success')
    return redirect(request.referrer or url_for('movimientos'))


@app.route('/movimientos/<int:row>/delete', methods=['POST'])
def delete_movimiento(row):
    eh.delete_movimiento(row)
    flash('Movimiento eliminado.', 'success')
    return redirect(request.referrer or url_for('movimientos'))


@app.route('/apuestas')
def apuestas():
    apuestas_list    = eh.load_apuestas()
    movimientos_list = eh.load_movimientos()
    mes_filter       = request.args.get('mes', '')
    casa_filter      = request.args.get('casa', '')
    resultado_filter = request.args.get('resultado', '')

    filtered = apuestas_list
    if mes_filter:       filtered = [a for a in filtered if a['mes_str'] == mes_filter]
    if casa_filter:      filtered = [a for a in filtered if a['casa'] == casa_filter]
    if resultado_filter: filtered = [a for a in filtered if a['resultado'] == resultado_filter]

    filtered = sorted(filtered, key=lambda a: (a['mes'] or datetime.min, a['fecha'] or datetime.min), reverse=True)
    available_months = sorted({a['mes_str'] for a in apuestas_list if a['mes_str']}, reverse=True)

    return render_template('apuestas.html',
        apuestas=filtered, available_months=available_months,
        bookmakers=eh.BOOKMAKERS, resultados=eh.RESULTADOS,
        shared_bookmakers=eh.SHARED_BOOKMAKERS, cuentas_apuesta=eh.CUENTAS_APUESTA,
        mes_filter=mes_filter, casa_filter=casa_filter, resultado_filter=resultado_filter,
    )


@app.route('/apuestas/add', methods=['POST'])
def add_apuesta():
    try:
        mes_str      = request.form.get('mes', '').strip()
        fecha_str    = request.form.get('fecha', '').strip()
        partido      = request.form.get('partido', '').strip()
        apuesta_desc = request.form.get('apuesta', '').strip()
        casa         = request.form['casa']
        stake        = float(request.form['stake'])
        cuota        = float(request.form['cuota'])
        resultado    = request.form.get('resultado', 'Pendiente')
        cuentas      = request.form.get('cuentas', 'Ambas')

        fecha = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else None
        if mes_str:
            mes_dt = datetime.strptime(mes_str + '-01', '%Y-%m-%d')
        elif fecha:
            mes_dt = datetime(fecha.year, fecha.month, 1)
        else:
            mes_dt = datetime(datetime.now().year, datetime.now().month, 1)

        eh.add_apuesta(mes_dt, fecha, partido, apuesta_desc, casa, stake, cuota, resultado, cuentas)
        flash('Apuesta añadida.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('apuestas'))


@app.route('/apuestas/<int:row>/edit', methods=['POST'])
def edit_apuesta(row):
    try:
        mes_str      = request.form.get('mes', '').strip()
        fecha_str    = request.form.get('fecha', '').strip()
        partido      = request.form.get('partido', '').strip()
        apuesta_desc = request.form.get('apuesta', '').strip()
        casa         = request.form['casa']
        stake        = float(request.form['stake'])
        cuota        = float(request.form['cuota'])
        resultado    = request.form.get('resultado', 'Pendiente')
        cuentas      = request.form.get('cuentas', 'Ambas')

        fecha = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else None
        if mes_str:
            mes_dt = datetime.strptime(mes_str + '-01', '%Y-%m-%d')
        elif fecha:
            mes_dt = datetime(fecha.year, fecha.month, 1)
        else:
            mes_dt = datetime(datetime.now().year, datetime.now().month, 1)

        eh.update_apuesta(row, mes_dt, fecha, partido, apuesta_desc, casa, stake, cuota, resultado, cuentas)
        flash('Apuesta actualizada.', 'success')
    except Exception as e:
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('apuestas'))


@app.route('/apuestas/<int:row>/resultado', methods=['POST'])
def update_resultado(row):
    resultado = request.form.get('resultado')
    if resultado in ('W', 'L', 'V', 'Pendiente'):
        eh.update_apuesta_resultado(row, resultado)
        flash(f'Resultado → {resultado}.', 'success')
    return redirect(request.referrer or url_for('apuestas'))


@app.route('/apuestas/<int:row>/delete', methods=['POST'])
def delete_apuesta(row):
    eh.delete_apuesta(row)
    flash('Apuesta eliminada.', 'success')
    return redirect(request.referrer or url_for('apuestas'))


@app.route('/historial')
def historial():
    movimientos_list = eh.load_movimientos()
    apuestas_list    = eh.load_apuestas()
    all_rows         = eh.compute_historial(movimientos_list, apuestas_list)
    available_months = [r['mes'] for r in all_rows]
    selected_months  = request.args.getlist('mes')
    rows = [r for r in all_rows if r['mes'] in selected_months] if selected_months else all_rows
    rows_cuenta = eh.compute_ganancias_por_cuenta(movimientos_list, apuestas_list)
    return render_template('historial.html',
        rows=rows, available_months=available_months,
        selected_months=selected_months, bookmakers=eh.BOOKMAKERS + ['YAAS'],
        rows_cuenta=rows_cuenta,
    )


# ── JSON API ───────────────────────────────────────────────────────────────────

@app.route('/api/steps30')
def api_steps30():
    return jsonify(gc.steps_last_n(30))

@app.route('/api/weekly')
def api_weekly():
    return jsonify(gc.weekly_volume(24))

@app.route('/api/vo2max')
def api_vo2max():
    return jsonify(gc.vo2max_history(365))

@app.route('/api/rhr')
def api_rhr():
    return jsonify(gc.resting_hr_history(60))


if __name__ == '__main__':
    app.run(debug=True, port=5000)
