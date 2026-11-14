"""普通网页采购前同步孔夫子默认地址；不创建采购、不提交订单、不付款。"""
import hashlib
import json
import datetime
import os
import uuid
import re
from urllib.parse import urlsplit

import config
import models
from services.checkout_task import fresh_receiver, canonical_source_url, valid_source_url
from services.kongfz_monitor import _address_key

KEY = 'kongfz_address_slot'
ORIGIN = 'https://user.kongfz.com'
BASE = ORIGIN + '/pc-gw/user-service/client/pc/address'
PAGE = ORIGIN + '/user-center/address/'


class AddressSyncError(ValueError):
    pass


def _region(province, city, district):
    province, city, district = (str(v or '') for v in (province, city, district))
    # 直辖市在两个平台可能分别占两级或一级；仅折叠明确的重复行政层级。
    if province.removesuffix('市') in ('北京', '上海', '天津', '重庆'):
        province = province.removesuffix('市') + '市'
        if city in (province, province.removesuffix('市')):
            city = ''
        if district == city:
            district = ''
    return province + city + district


def _full(receiver):
    return _region(receiver.get('prov_name'), receiver.get('city_name'), receiver.get('area_name')) + \
        (receiver.get('town_name') or '') + (receiver.get('address') or '')


def _row_full(row):
    area = row.get('areaInfo') or {}
    return _region(area.get('provName'), area.get('cityName'), area.get('areaName')) + (row.get('address') or '')


def _fingerprint(name, phone, address):
    return hashlib.sha256(json.dumps([str(name).strip(), str(phone).strip(),
                                     _address_key(address)], ensure_ascii=False).encode()).hexdigest()


def row_fingerprint(row):
    return _fingerprint(row.get('receiverName', ''), row.get('mobile', ''), _row_full(row))


def receiver_fingerprint(receiver):
    return _fingerprint(receiver['receiver_name'], receiver['receiver_mobile'], _full(receiver))


def _default(row):
    return row.get('isDefault') in (1, '1', True)


def _load():
    return json.loads(models.get_setting(KEY, '{}'))


def _save(state):
    models.set_setting(KEY, json.dumps(state, ensure_ascii=False))


def _request(context, method, url, **kwargs):
    # 固定官方地址且禁止重定向；不把收货信息、平台原始异常或凭据写入日志。
    try:
        response = context.request.fetch(url, method=method, timeout=12000, max_redirects=0,
                                         headers={'Origin': ORIGIN, 'Referer': PAGE}, **kwargs)
        result = response.json()
        if not response.ok or result.get('status') != 1:
            raise AddressSyncError('孔夫子未接受地址请求，请检查登录状态后重试')
        return result
    except AddressSyncError:
        raise
    except Exception:
        raise AddressSyncError('孔夫子地址请求结果未确认，请稍后核对') from None


def _list(context):
    rows = _request(context, 'GET', BASE + '/list').get('data')
    if (not isinstance(rows, list) or any(not isinstance(row, dict)
            or not re.fullmatch(r'\d+', str(row.get('addrId', ''))) for row in rows)):
        raise AddressSyncError('孔夫子地址列表格式变化，已停止同步')
    return rows


def _resolve_pending(rows, state):
    pending = state.get('pending')
    if not pending:
        return
    matches = [r for r in rows if row_fingerprint(r) == pending['fingerprint'] and _default(r)]
    if len(matches) != 1:
        raise AddressSyncError('上次地址写入结果仍待核对，已停止重复请求，请联系检查')
    row = matches[0]
    action = pending['action']
    if ((action != 'create' and str(row['addrId']) != pending['id'])
            or (action == 'create' and str(row['addrId']) in pending['old_ids'])):
        raise AddressSyncError('上次地址写入归属未确认，已停止重复请求')
    if action in ('create', 'update'):
        state['slot'] = dict(id=str(row['addrId']), user_id=str(row['userId']),
                             fingerprint=row_fingerprint(row))
    state.pop('pending')
    _save(state)


def adopt_default(context):
    """仅部署时按用户明确授权，将现有默认地址登记为专用位置；不改远端地址。"""
    rows = _list(context)
    state = _load()
    if state.get('slot') or state.get('pending'):
        raise AddressSyncError('已有专用地址记录，不能重复接管')
    defaults = [r for r in rows if _default(r)]
    if len(defaults) != 1:
        raise AddressSyncError('当前默认地址不唯一，不能登记专用位置')
    row = defaults[0]
    state['slot'] = dict(id=str(row['addrId']), user_id=str(row['userId']),
                         fingerprint=row_fingerprint(row))
    _save(state)
    return dict(address_id=row['addrId'], count=len(rows))


def _official_area_data(context):
    # 与孔夫子手动地区选择器使用同一份官方数据；仅解析JSON，绝不执行JS。
    try:
        response = context.request.get('https://res.kongfz.com/js/data/area.js',
                                       timeout=12000, max_redirects=0)
        source = response.text()
        match = re.fullmatch(r'\s*var\s+___AREADATA\s*=\s*(\{.*\})\s*;?\s*', source, re.S)
        if not response.ok or len(source) > 2000000 or not match:
            raise ValueError('地区数据不可用')
        data = json.loads(match.group(1))
        if data.get('status') != 1:
            raise ValueError('地区数据不可用')
        return data['result']
    except Exception:
        raise AddressSyncError('孔夫子省市区数据暂不可用，未保存地址，请稍后重试') from None


def _official_region(context, receiver):
    data = _official_area_data(context)
    try:
        provinces = [row for group in data['province'].values() for row in group]
        province, city, area = (str(receiver.get(key) or '') for key in ('prov_name','city_name','area_name'))
        municipal = province.removesuffix('市') in ('北京','上海','天津','重庆')
        if municipal:
            province = province.removesuffix('市') + '市'
            if city in (province, province.removesuffix('市')):
                city, area = area, ''
        candidates = [row for row in provinces if _address_key(row['address']) == _address_key(province)]
        if len(candidates) != 1:
            raise ValueError('省份不唯一')
        prov = candidates[0]

        def child(parent, name):
            matches = [(code, value) for code, value in data['cities'].get(str(parent), {}).items()
                       if name and _address_key(value) == _address_key(name)]
            if len(matches) != 1:
                raise ValueError('地区不唯一')
            return matches[0]

        city_id, city_name = child(prov['code'], city)
        area_id, area_name = child(city_id, area) if area else ('', '')
        if data['cities'].get(str(area_id or city_id)):
            raise ValueError('未到末级地区')
        return dict(provId=prov['code'], provName=prov['address'], cityId=city_id,
                    cityName=city_name, areaId=area_id, areaName=area_name, isLeafArea=True)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AddressSyncError('订单省市区无法在孔夫子地区表中唯一匹配，未保存地址') from None


def _checked_region_id(result, receiver):
    region_matches = all(_address_key(result.get(parsed)) == _address_key(receiver.get(original))
                         for parsed, original in (('provName', 'prov_name'), ('cityName', 'city_name'),
                                                  ('areaName', 'area_name')))
    municipality_leaf = (str(result.get('provName') or '').removesuffix('市') in ('北京','上海','天津','重庆')
                         and receiver.get('area_name') and not result.get('areaName')
                         and _address_key(result.get('cityName')) == _address_key(receiver['area_name']))
    if municipality_leaf:
        region_matches = _address_key(_region(result.get('provName'),result.get('cityName'),result.get('areaName'))) == \
            _address_key(_region(receiver.get('prov_name'),receiver.get('city_name'),receiver.get('area_name')))
    city_level = (receiver.get('prov_name') == '广东省' and receiver.get('city_name') == '中山市'
                  and not receiver.get('area_name') and not result.get('areaName')
                  and str(result.get('provId')) == '8000000000'
                  and str(result.get('cityId')) == '8017000000')
    region_id = result.get('areaId') or (result.get('cityId') if city_level or municipality_leaf else None)
    if (not result.get('isLeafArea') or not result.get('provId') or not result.get('cityId')
            or not region_id or not region_matches):
        return None
    return region_id


def _payload(context, receiver):
    # 智能识别失败时逐级精确查官方地区表；街道和门牌始终保留闲鱼原文。
    source = receiver['receiver_name'] + '，' + receiver['receiver_mobile'] + '，' + _full(receiver)
    result = _request(context, 'POST', ORIGIN + '/pc-gw/express-web/pc/v1/address/parse',
                      params={'source': source}, data={}).get('result') or {}
    region_id = _checked_region_id(result, receiver)
    if region_id is None:
        region_id = _checked_region_id(_official_region(context, receiver), receiver)
    if region_id is None:
        raise AddressSyncError('孔夫子地区与订单省市区不一致，未保存地址')
    detail = (receiver.get('town_name') or '') + (receiver.get('address') or '')
    if not 2 <= len(receiver['receiver_name']) <= 30 or not detail or len(detail) > 200:
        raise AddressSyncError('收货信息不符合孔夫子地址格式，未保存地址')
    return dict(receiverName=receiver['receiver_name'], mobile=receiver['receiver_mobile'],
                phoneNum='', zipCode='', area=region_id, address=detail, isDefault=1)


def _private_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())


def _finish_rotation(rows, state):
    pending = state.get('rotation_pending')
    if not pending:
        return
    actual = {str(r['addrId']): row_fingerprint(r) for r in rows}
    if actual != pending['remaining']:
        raise AddressSyncError('上次旧地址清理结果尚未核对，已暂停新增，请联系检查')
    receipt = config.DATA_DIR / 'backups' / pending['backup'] / 'receipt.json'
    if not receipt.exists():
        _private_json(receipt, dict(verified=True, removed_id=pending['id'],
                                   remaining_ids=list(actual), purchase_submitted=False, paid=False))
    state.pop('rotation_pending')
    _save(state)


def _rotate_oldest(context, rows, state, recheck):
    # 使用官方保存时间排序，不以默认标记、列表位置或编号推测时间。
    try:
        oldest = min(rows, key=lambda row: (
            datetime.datetime.strptime(row['addTime'], '%Y-%m-%d %H:%M:%S'), int(row['addrId'])))
    except (KeyError, TypeError, ValueError):
        raise AddressSyncError('孔夫子地址保存时间不完整，未清理任何地址') from None
    name = 'address-rotation-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    directory = config.DATA_DIR / 'backups' / name
    try:
        directory.mkdir(mode=0o700, parents=True)
        _private_json(directory / 'before.json', dict(rows=rows, state=state, remove_id=str(oldest['addrId'])))
    except OSError:
        raise AddressSyncError('旧地址备份失败，未清理地址') from None
    recheck()
    state['rotation_pending'] = dict(id=str(oldest['addrId']), backup=name,
        remaining={str(r['addrId']): row_fingerprint(r) for r in rows if r['addrId'] != oldest['addrId']})
    _save(state)  # 记录具体删除编号；超时只回读，不再删除第二条。
    try:
        _request(context, 'DELETE', BASE + '/multi/' + str(oldest['addrId']))
    except AddressSyncError:
        pass
    remaining = _list(context)
    _finish_rotation(remaining, state)
    return remaining


def sync_in_context(context, receiver, recheck=lambda: None):
    """调用方须持有浏览器锁；每次最多发出一次地址写请求，随后只读核对。"""
    rows = _list(context)
    state = _load()
    account = (state.get('slot') or {}).get('user_id')
    if state.get('mode') == 'per_order' and (not rows or not account or any(str(r.get('userId')) != account for r in rows)):
        raise AddressSyncError('孔夫子账号与采购地址所属账号不一致，已停止同步')
    _finish_rotation(rows, state)
    _resolve_pending(rows, state)
    expected = receiver_fingerprint(receiver)
    matches = [r for r in rows if row_fingerprint(r) == expected]
    slot = state.get('slot') or {}
    per_order = state.get('mode') == 'per_order'
    target = next((r for r in rows if str(r['addrId']) == slot.get('id')
                   and str(r.get('userId')) == slot.get('user_id')), None)
    if per_order:
        # 每份收货信息使用独立地址，不改写其他已打开结算页引用的地址编号。
        target = None
    elif slot:
        # 兼容修正直辖市层级前登记的内容指纹，仍须与原文完整一致。
        legacy_fingerprint = None
        if target:
            info = target.get('areaInfo') or {}
            legacy_full = ''.join(str(info.get(k) or '') for k in ('provName','cityName','areaName')) + (target.get('address') or '')
            legacy_fingerprint = _fingerprint(target.get('receiverName',''),target.get('mobile',''),legacy_full)
        if target is None or slot['fingerprint'] not in (row_fingerprint(target), legacy_fingerprint):
            raise AddressSyncError('专用地址被手动修改或孔夫子账号已切换，已停止覆盖，请联系检查')
        # 商品页会记住地址编号；已有专用位置时不得切换到其他同内容地址。
        matches = [target] if row_fingerprint(target) == expected else []
    if matches:
        chosen = next((r for r in matches if _default(r)), matches[0])
        action, method, url, kwargs = 'default', 'POST', BASE + '/setDefault/' + str(chosen['addrId']), {}
        if _default(chosen) and sum(_default(r) for r in rows) == 1:
            recheck()
            return dict(address_id=chosen['addrId'], action='reused', verified=True)
    else:
        rotate = not target and len(rows) >= 20 and per_order and state.get('rotation_policy') == 'oldest'
        if not target and len(rows) >= 20 and not rotate:
            raise AddressSyncError('孔夫子地址已满20条，已暂停新增，请先备份处理旧地址；系统不会擅自删除地址')
        payload = _payload(context, receiver)
        if rotate:
            if len(rows) != 20:
                raise AddressSyncError('孔夫子地址数量异常，未清理地址')
            rows = _rotate_oldest(context, rows, state, recheck)
        chosen = target
        action = 'update' if target else 'create'
        method = 'PUT' if target else 'POST'
        url = BASE + ('/' + str(target['addrId']) if target else '')
        kwargs = {'data': payload}
    recheck()  # 写入前再次核对闲鱼状态和最新收货信息。
    state['pending'] = dict(action=action, id=str(chosen['addrId']) if chosen else '',
                            old_ids=[str(r['addrId']) for r in rows], fingerprint=expected)
    _save(state)  # 超时、进程中断后也不能盲目重复写入。
    try:
        _request(context, method, url, **kwargs)
    except AddressSyncError:
        pass  # 成败以回读结果为准，不重试写请求。
    verified = _list(context)
    if sum(_default(r) for r in verified) != 1:
        raise AddressSyncError('默认地址回读不唯一，已停止跳转，请联系检查')
    _resolve_pending(verified, state)
    row = next(r for r in verified if row_fingerprint(r) == expected and _default(r))
    return dict(address_id=row['addrId'], action=action, verified=True)


def source_order(order_id):
    order = models.query('SELECT * FROM orders WHERE id=?', (order_id,), one=True)
    if not order or order['status'] not in ('pending', 'purchasing'):
        raise AddressSyncError('这笔订单当前不能采购，请返回刷新订单')
    from services.order_service import list_orders
    orders, _ = list_orders(keyword=order['order_no'], size=50)
    source = next((r['jump_url'] for r in orders if r['id'] == order_id), '')
    source = canonical_source_url(source)
    try:
        valid = valid_source_url(source)
    except ValueError:
        valid = False
    if not valid:
        raise AddressSyncError('未找到有效的孔夫子货源入口')
    return order, source


def prepare(order_id):
    if not config.CLOUD_MODE or config.DEMO_MODE:
        raise AddressSyncError('免插件地址同步仅用于正式服务器')
    from services.process_lock import acquire
    from playwright.sync_api import sync_playwright
    from bots.kongfz_bot import _launch
    try:
        lock = acquire(config.DATA_DIR / 'locks' / 'native-address.lock', '正在同步另一笔地址，请稍后重试')
    except RuntimeError:
        raise AddressSyncError('正在同步另一笔地址，请稍后重试') from None
    try:
        order, source = source_order(order_id)
        def latest(current):
            try:
                return fresh_receiver(current, allow_city_level=True)
            except ValueError as error:
                raise AddressSyncError(str(error)) from None

        receiver = latest(order)

        def recheck():
            current = models.query('SELECT * FROM orders WHERE id=?', (order_id,), one=True)
            if not current or current['status'] not in ('pending', 'purchasing'):
                raise AddressSyncError('订单状态已变化，已停止地址同步')
            if receiver_fingerprint(latest(current)) != receiver_fingerprint(receiver):
                raise AddressSyncError('买家收货信息已变化，请重新从订单打开货源')

        with sync_playwright() as runtime:
            def authenticate(context):
                page = context.new_page()
                page.goto(PAGE, wait_until='domcontentloaded', timeout=20000)
                if urlsplit(page.url).hostname == 'login.kongfz.com':
                    from services.kongfz_auth import try_login
                    if not try_login(page):
                        raise AddressSyncError('孔夫子需要登录或验证码，请到设置页完成登录后重试')
            from services.kongfz_address_session import open_session
            with open_session(runtime,lambda: _launch(runtime),authenticate,_list) as context:
                result = sync_in_context(context, receiver, recheck)
                recheck()
                return dict(ok=True, source_url=source, **result)
    finally:
        lock.close()
