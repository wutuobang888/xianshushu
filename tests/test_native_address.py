"""免插件地址同步：模拟官方接口，不使用真实账号、订单或地址。"""
import copy
import json
import os
from pathlib import Path
import re
import unittest
import tempfile
import datetime
from unittest.mock import patch, Mock

from services import kongfz_address as address

RECEIVER = dict(receiver_name='测试买家', receiver_mobile='13800000000', prov_name='浙江省',
                city_name='杭州市', area_name='西湖区', town_name='测试街道', address='测试路1号')


def row(number=1, default=False, detail='测试街道测试路1号', name='测试买家'):
    return dict(addrId=number, userId=100, receiverName=name, mobile='13800000000',
                addTime=(datetime.datetime(2026,1,1)+datetime.timedelta(seconds=number)).strftime('%Y-%m-%d %H:%M:%S'),
                address=detail, area=330106, isDefault=int(default),
                areaInfo=dict(provName='浙江省', cityName='杭州市', areaName='西湖区'))


class API:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)
        self.writes = []
        self.fail_after_write = False
        self.skip_write = False
        self.parse_detail = '测试街道测试路1号'
        self.parse_area = '西湖区'
        self.corrupt_saved_detail = False
        self.next_id = 99
        self.skip_delete = False

    def __call__(self, context, method, url, **kwargs):
        if url.endswith('/list'):
            return {'data': copy.deepcopy(self.rows)}
        if url.endswith('/parse'):
            return {'result':dict(isLeafArea=True, provId=33, cityId=3301, areaId=330106,
                    provName='浙江省', cityName='杭州市', areaName=self.parse_area, address=self.parse_detail)}
        self.writes.append((method, url, kwargs))
        if method == 'DELETE':
            if not self.skip_delete:
                self.rows=[r for r in self.rows if str(r['addrId']) != url.rsplit('/',1)[1]]
            if self.fail_after_write:
                raise address.AddressSyncError('模拟删除响应丢失')
            return {'status':1}
        if not self.skip_write:
            for old in self.rows:
                old['isDefault'] = 0
            if '/setDefault/' in url:
                next(r for r in self.rows if str(r['addrId']) == url.rsplit('/',1)[1])['isDefault'] = 1
            elif method == 'POST':
                self.rows.insert(0, row(self.next_id,True,kwargs['data']['address'],kwargs['data']['receiverName']))
                self.next_id += 1
            elif method == 'PUT':
                target=next(r for r in self.rows if str(r['addrId']) == url.rsplit('/',1)[1])
                target.update(kwargs['data'])
            if self.corrupt_saved_detail:
                next(r for r in self.rows if r['isDefault'])['address']='测试街道测试路2号'
        if self.fail_after_write or self.skip_write:
            raise address.AddressSyncError('模拟连接中断')
        return {'status':1}


class NativeAddressTests(unittest.TestCase):
    def rotation(self, rows):
        self.per_order(rows[0])
        state=self.state();state['rotation_policy']='oldest';address._save(state)
        self.backups=Path(tempfile.mkdtemp(prefix='xss-address-rotation-'))
        patcher=patch.object(address.config,'DATA_DIR',self.backups)
        patcher.start();self.addCleanup(patcher.stop)
        return API(rows)

    def test_rotation_handles_45_distinct_orders_and_reuses_at_capacity(self):
        api=self.rotation([row(1,True,detail='旧地址')])
        with patch.object(address,'_request',side_effect=api):
            for i in range(45):
                receiver=dict(RECEIVER,receiver_name='测试买家'+str(i))
                result=address.sync_in_context(None,receiver)
                self.assertTrue(result['verified'])
                self.assertLessEqual(len(api.rows),20)
                self.assertEqual(address.row_fingerprint(next(r for r in api.rows if r['isDefault'])),address.receiver_fingerprint(receiver))
            count=len(api.writes)
            self.assertEqual(address.sync_in_context(None,receiver)['action'],'reused')
            self.assertEqual(len(api.writes),count)
        self.assertEqual(len(api.rows),20)
        deletes=[w for w in api.writes if w[0]=='DELETE']
        self.assertEqual(len(deletes),26)
        self.assertEqual(len(list(self.backups.glob('backups/*/receipt.json'))),26)

    def test_rotation_uses_official_time_not_id_or_list_position(self):
        rows=[row(i,default=i==20,detail='旧地址'+str(i)) for i in range(1,21)]
        rows[14]['addTime']='2025-01-01 00:00:00'
        api=self.rotation(rows)
        self.run_sync(api)
        self.assertEqual(api.writes[0][0],'DELETE')
        self.assertTrue(api.writes[0][1].endswith('/multi/15'))
        for old in rows:
            if old['addrId']!=15:
                saved=next(r for r in api.rows if r['addrId']==old['addrId'])
                self.assertEqual(address.row_fingerprint(saved),address.row_fingerprint(old))

    def test_rotation_backup_failure_does_not_delete(self):
        api=self.rotation([row(i,detail='旧'+str(i)) for i in range(1,21)])
        with patch.object(address,'_private_json',side_effect=OSError('磁盘满')):
            with self.assertRaisesRegex(address.AddressSyncError,'备份失败'):self.run_sync(api)
        self.assertEqual(api.writes,[])

    def test_rotation_missing_time_does_not_delete(self):
        rows=[row(i,detail='旧'+str(i)) for i in range(1,21)]
        rows[3].pop('addTime')
        api=self.rotation(rows)
        with self.assertRaisesRegex(address.AddressSyncError,'时间不完整'):self.run_sync(api)
        self.assertEqual(api.writes,[])

    def test_rotation_unconfirmed_delete_blocks_creates_and_further_deletes(self):
        api=self.rotation([row(i,detail='旧'+str(i)) for i in range(1,21)])
        api.skip_delete=True
        for _ in range(2):
            with self.assertRaisesRegex(address.AddressSyncError,'清理结果尚未核对'):self.run_sync(api)
        self.assertEqual(len(api.writes),1)
        self.assertEqual(api.writes[0][0],'DELETE')

    def test_rotation_lost_delete_response_is_verified_before_create(self):
        api=self.rotation([row(i,detail='旧'+str(i)) for i in range(1,21)])
        api.fail_after_write=True
        result=self.run_sync(api)
        self.assertTrue(result['verified'])
        self.assertEqual([w[0] for w in api.writes],['DELETE','POST'])
        self.assertNotIn('rotation_pending',self.state())

    def test_rotation_recheck_failure_does_not_delete(self):
        api=self.rotation([row(i,detail='旧'+str(i)) for i in range(1,21)])
        with self.assertRaisesRegex(address.AddressSyncError,'订单变化'):
            self.run_sync(api,recheck=Mock(side_effect=address.AddressSyncError('订单变化')))
        self.assertEqual(api.writes,[])

    def per_order(self, original):
        self.managed(original)
        state=self.state();state['mode']='per_order';address._save(state)

    def test_per_order_keeps_previous_checkout_address_unchanged(self):
        original=row(1,True,detail='第一笔买家的地址',name='第一笔买家')
        self.per_order(original)
        api=API([original])
        result=self.run_sync(api)
        self.assertEqual(result['action'],'create')
        first=next(r for r in api.rows if r['addrId']==1)
        self.assertEqual(first['address'],original['address'])
        self.assertEqual(first['receiverName'],original['receiverName'])
        self.assertEqual(api.writes[0][0],'POST')
        self.assertEqual(len(api.rows),2)

    def test_per_order_reuses_matching_address_with_no_duplicate(self):
        original=row(1,True,detail='其他买家的地址')
        self.per_order(original)
        api=API([original,row(2)])
        result=self.run_sync(api)
        self.assertEqual(result['address_id'],2)
        self.assertEqual(len(api.rows),2)
        self.assertIn('/setDefault/2',api.writes[0][1])

    def test_per_order_full_book_stops_without_deleting_or_overwriting(self):
        original=row(1,True,detail='旧地址')
        self.per_order(original)
        api=API([original]+[row(i,detail='其他地址'+str(i)) for i in range(2,21)])
        with self.assertRaisesRegex(address.AddressSyncError,'已满20条'):self.run_sync(api)
        self.assertEqual(api.writes,[])

    def test_per_order_wrong_account_does_not_write(self):
        original=row(1,True,detail='旧地址')
        self.per_order(original)
        api=API([dict(original,userId=200)])
        with self.assertRaisesRegex(address.AddressSyncError,'账号'):self.run_sync(api)
        self.assertEqual(api.writes,[])

    def test_municipality_district_is_city_leaf_and_fingerprint_matches(self):
        for province,district in [('重庆','涪陵区'),('北京市','海淀区'),('上海','浦东新区'),('天津市','和平区')]:
            receiver=dict(RECEIVER,prov_name=province,city_name=province.removesuffix('市')+'市',area_name=district)
            parsed=dict(isLeafArea=True,provId=4000000000,cityId=4019000000,areaId=None,
                        provName=province.removesuffix('市')+'市',cityName=district,areaName=None)
            with patch.object(address,'_request',return_value={'result':parsed}):
                payload=address._payload(None,receiver)
            self.assertEqual(payload['area'],4019000000)
            saved=row(default=True)
            saved['areaInfo']=parsed
            self.assertEqual(address.row_fingerprint(saved),address.receiver_fingerprint(receiver))

    def test_municipality_wrong_district_or_province_is_rejected(self):
        receiver=dict(RECEIVER,prov_name='重庆',city_name='重庆市',area_name='涪陵区')
        for province,district in [('重庆市','南岸区'),('天津市','涪陵区')]:
            parsed=dict(isLeafArea=True,provId=4000000000,cityId=4019000000,areaId=None,
                        provName=province,cityName=district,areaName=None)
            with patch.object(address,'_request',return_value={'result':parsed}):
                with self.assertRaises(address.AddressSyncError):address._payload(None,receiver)

    def test_municipality_non_leaf_remains_rejected(self):
        receiver=dict(RECEIVER,prov_name='重庆',city_name='重庆市',area_name='涪陵区')
        parsed=dict(isLeafArea=False,provId=4000000000,cityId=4019000000,areaId=None,
                    provName='重庆市',cityName='涪陵区',areaName=None)
        with patch.object(address,'_request',return_value={'result':parsed}):
            with self.assertRaises(address.AddressSyncError):address._payload(None,receiver)

    def test_city_leaf_without_district_uses_verified_city_code(self):
        receiver=dict(RECEIVER,prov_name='广东省',city_name='中山市',area_name='')
        parsed=dict(isLeafArea=True,provId=8000000000,cityId=8017000000,areaId=None,
                    provName='广东省',cityName='中山市',areaName=None)
        with patch.object(address,'_request',return_value={'result':parsed}):
            payload=address._payload(None,receiver)
        self.assertEqual(payload['area'],8017000000)
        self.assertEqual(payload['address'],receiver['town_name']+receiver['address'])

    def test_missing_district_does_not_accept_non_leaf_or_other_city(self):
        for city,leaf in [('中山市',False),('杭州市',True)]:
            receiver=dict(RECEIVER,prov_name='广东省',city_name=city,area_name='')
            parsed=dict(isLeafArea=leaf,provId=8000000000,cityId=8017000000,areaId=None,
                        provName='广东省',cityName=city,areaName=None)
            with patch.object(address,'_request',return_value={'result':parsed}):
                with self.assertRaises(address.AddressSyncError):address._payload(None,receiver)

    def test_city_leaf_requires_verified_official_city_code(self):
        receiver=dict(RECEIVER,prov_name='广东省',city_name='中山市',area_name='')
        parsed=dict(isLeafArea=True,provId=8000000000,cityId=999,areaId=None,
                    provName='广东省',cityName='中山市',areaName=None)
        with patch.object(address,'_request',return_value={'result':parsed}):
            with self.assertRaises(address.AddressSyncError):address._payload(None,receiver)

    def test_fresh_city_receiver_opt_in_only(self):
        from services.checkout_task import fresh_receiver
        from services import xgj_client
        remote=dict(RECEIVER,prov_name='广东省',city_name='中山市',area_name='')
        with patch.object(xgj_client.XgjClient,'order_detail',return_value=remote), \
             patch.object(xgj_client,'pick_order_no',return_value='test'), \
             patch.object(xgj_client,'map_remote_status',return_value='pending'), \
             patch.object(xgj_client,'pick_ship_status',return_value='12'), \
             patch.object(xgj_client,'pick_waybill',return_value=''), \
             patch.object(address.models,'execute'):
            with self.assertRaises(ValueError):fresh_receiver({'id':1,'order_no':'test'})
            result=fresh_receiver({'id':1,'order_no':'test'},allow_city_level=True)
            self.assertEqual(result['area_name'],'')
            remote['city_name']='杭州市'
            with self.assertRaises(ValueError):fresh_receiver({'id':1,'order_no':'test'},allow_city_level=True)

    def setUp(self):
        self.settings = {}
        self.patches = [patch.object(address.models,'get_setting',side_effect=lambda key,default='':self.settings.get(key,default)),
                        patch.object(address.models,'set_setting',side_effect=lambda key,value:self.settings.update({key:value}))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def state(self):
        return json.loads(self.settings.get(address.KEY,'{}'))

    def managed(self, original):
        address._save({'slot':{'id':str(original['addrId']),'user_id':str(original['userId']),
                              'fingerprint':address.row_fingerprint(original)}})

    def run_sync(self, api, recheck=lambda:None):
        with patch.object(address,'_request',side_effect=api):
            return address.sync_in_context(None,RECEIVER,recheck)

    def test_exact_existing_address_becomes_default_without_duplicate(self):
        api=API([row(1,True,detail='其他地址'),row(2)])
        result=self.run_sync(api)
        self.assertEqual(result['address_id'],2)
        self.assertEqual(len(api.rows),2)
        self.assertEqual(len(api.writes),1)
        self.assertIn('/setDefault/2',api.writes[0][1])
        self.assertEqual(self.run_sync(api)['action'],'reused')
        self.assertEqual(len(api.writes),1)

    def test_authorized_slot_is_updated_without_deleting_other_addresses(self):
        original=row(1,True,detail='旧的专用地址')
        self.managed(original)
        api=API([original]+[row(i,detail='其他地址'+str(i)) for i in range(2,21)])
        result=self.run_sync(api)
        self.assertEqual(result['action'],'update')
        self.assertEqual(len(api.rows),20)
        self.assertEqual(api.writes[0][0],'PUT')
        self.assertNotIn('pending',self.state())
        self.assertEqual(self.state()['slot']['fingerprint'],address.receiver_fingerprint(RECEIVER))

    def test_create_and_next_click_reuses_same_address(self):
        api=API([row(1,True,detail='其他地址')])
        result=self.run_sync(api)
        self.assertEqual(result['action'],'create')
        self.assertEqual(self.state()['slot']['id'],'99')
        self.run_sync(api)
        self.assertEqual(len(api.writes),1)

    def test_managed_slot_is_updated_even_when_another_address_matches(self):
        original=row(1,True,detail='上笔订单地址')
        self.managed(original)
        api=API([original,row(2)])
        result=self.run_sync(api)
        self.assertEqual(result['address_id'],1)
        self.assertEqual(result['action'],'update')
        self.assertEqual(len(api.rows),2)
        self.assertEqual(api.rows[1],row(2))
        self.assertEqual(self.state()['slot']['id'],'1')

    def test_matching_managed_slot_is_selected_instead_of_other_default(self):
        original=row(1)
        self.managed(original)
        api=API([row(2,True),original])
        result=self.run_sync(api)
        self.assertEqual(result['address_id'],1)
        self.assertIn('/setDefault/1',api.writes[0][1])
        self.assertEqual(len(api.writes),1)

    def test_account_guard_cannot_be_bypassed_by_matching_other_address(self):
        original=row(1,True,detail='旧地址')
        self.managed(original)
        api=API([dict(row(2,True),userId=200)])
        with self.assertRaisesRegex(address.AddressSyncError,'账号已切换'):
            self.run_sync(api)
        self.assertFalse(api.writes)

    def test_full_without_authorized_slot_never_writes(self):
        api=API([row(i,detail='其他地址'+str(i)) for i in range(20)])
        with self.assertRaisesRegex(address.AddressSyncError,'已满20条'):
            self.run_sync(api)
        self.assertFalse(api.writes)

    def test_manually_changed_slot_or_account_switch_cannot_be_overwritten(self):
        original=row(1,True,detail='专用地址')
        self.managed(original)
        for changed in (dict(original,address='用户手工修改'),dict(original,userId=200)):
            api=API([changed])
            with self.assertRaisesRegex(address.AddressSyncError,'手动修改或孔夫子账号'):
                self.run_sync(api)
            self.assertFalse(api.writes)

    def test_parser_changed_door_number_is_ignored_and_original_is_saved(self):
        api=API([])
        api.parse_detail='测试街道测试路2号'
        self.assertTrue(self.run_sync(api)['verified'])
        self.assertEqual(api.writes[0][2]['data']['address'],'测试街道测试路1号')

    def test_parser_removing_street_or_duplicate_city_cannot_drop_original_text(self):
        for original_detail, parsed_detail in (
                ('测试街道测试路1号','测试路1号'),
                ('测试街道杭州市测试路1号','测试街道测试路1号')):
            with self.subTest(original_detail=original_detail):
                receiver=dict(RECEIVER,town_name='',address=original_detail)
                api=API([])
                api.parse_detail=parsed_detail
                self.settings.clear()
                with patch.object(address,'_request',side_effect=api):
                    self.assertTrue(address.sync_in_context(None,receiver)['verified'])
                self.assertEqual(api.writes[0][2]['data']['address'],original_detail)

    def test_wrong_region_from_parser_never_saves(self):
        api=API([])
        api.parse_area='其他区'
        with self.assertRaisesRegex(address.AddressSyncError,'省市区'):
            self.run_sync(api)
        self.assertFalse(api.writes)

    def test_changed_door_number_on_readback_still_blocks_success(self):
        api=API([])
        api.corrupt_saved_detail=True
        with self.assertRaisesRegex(address.AddressSyncError,'待核对'):
            self.run_sync(api)
        self.assertIn('pending',self.state())

    def test_remote_state_rechecked_before_any_address_write(self):
        api=API([])
        check=Mock(side_effect=address.AddressSyncError('订单已退款'))
        with self.assertRaisesRegex(address.AddressSyncError,'退款'):
            self.run_sync(api,check)
        self.assertFalse(api.writes)

    def test_connection_dropped_after_success_readback_resolves_without_retry(self):
        api=API([])
        api.fail_after_write=True
        self.assertTrue(self.run_sync(api)['verified'])
        self.assertEqual(len(api.writes),1)
        self.assertNotIn('pending',self.state())

    def test_unknown_result_persists_guard_and_blocks_duplicate_write(self):
        api=API([])
        api.skip_write=True
        for _ in range(2):
            with self.assertRaisesRegex(address.AddressSyncError,'待核对|回读不唯一'):
                self.run_sync(api)
        self.assertEqual(len(api.writes),1)
        self.assertIn('pending',self.state())
        self.assertNotIn('13800000000',self.settings[address.KEY])
        self.assertNotIn('测试街道',self.settings[address.KEY])

    def test_adoption_only_records_current_default_and_never_calls_write(self):
        api=API([row(1,True),row(2)])
        with patch.object(address,'_request',side_effect=api):
            result=address.adopt_default(None)
        self.assertEqual(result['address_id'],1)
        self.assertEqual(self.state()['slot']['id'],'1')
        self.assertFalse(api.writes)

    def test_anonymous_read_page_does_not_sync(self):
        from flask import Flask
        from routes.order_routes import bp
        app=Flask(__name__)
        app.register_blueprint(bp)
        with patch('routes.order_routes.render_template',return_value='准备页'), \
                patch.object(address.config,'CLOUD_MODE',True), patch.object(address,'prepare') as prepare:
            self.assertEqual(app.test_client().get('/orders/1/source').status_code,200)
            prepare.assert_not_called()


class NativeAddressBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(headless=True,channel='msedge' if os.name=='nt' else None)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def page(self, result):
        root=Path(__file__).resolve().parents[1]
        template=(root/'templates/source_prepare.html').read_text(encoding='utf-8')
        code=re.search(r'<script>(.*?)</script>',template,re.S).group(1)
        code=re.sub(r'\{\{.*?\}\}',json.dumps('/orders/1/sync_address'),code)
        security=(root/'static/cloud-security.js').read_text(encoding='utf-8')
        html='<h5>同步中</h5><p id="source-message"></p><button id="source-retry" hidden>重新检查</button>'
        html+='<script data-csrf="offline-token">'+security+'</script><script>'+code+'</script>'
        page=self.browser.new_page()
        self.addCleanup(page.close)
        writes=[]
        def route(request):
            if request.request.url.endswith('/sync_address'):
                writes.append(request.request)
                request.fulfill(json=result,status=200 if result['ok'] else 400)
            elif request.request.url=='https://book.kongfz.com/1/2/':
                request.fulfill(body='<p>官方货源测试页</p>')
            else:
                request.fulfill(body=html,content_type='text/html')
        page.route('**/*',route)
        page.goto('https://offline.test/orders/1/source')
        return page,writes

    def test_no_plugin_post_has_csrf_then_redirects_to_plain_official_page(self):
        page,writes=self.page(dict(ok=True,source_url='https://book.kongfz.com/1/2/'))
        page.wait_for_url('https://book.kongfz.com/1/2/')
        self.assertEqual(len(writes),1)
        self.assertEqual(writes[0].method,'POST')
        self.assertEqual(writes[0].headers['x-csrf-token'],'offline-token')
        self.assertNotIn('xss_ctx',page.url)

    def test_failure_stays_on_site_and_never_opens_source(self):
        page,writes=self.page(dict(ok=False,error='地址核对失败'))
        page.get_by_text('地址核对失败',exact=True).wait_for()
        self.assertTrue(page.url.startswith('https://offline.test/'))
        self.assertTrue(page.locator('#source-retry').is_visible())
        self.assertEqual(len(writes),1)


if __name__ == '__main__':
    unittest.main()
