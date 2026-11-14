"""官方地区树回退：仅使用隔离的虚构收货信息，不发送真实订单。"""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from services import kongfz_address as address


RECEIVER = dict(receiver_name='测试收货人', receiver_mobile='13800000000',
                prov_name='湖北省', city_name='恩施土家族苗族自治州', area_name='恩施市',
                town_name='测试街道', address='测试路26号')
TREE = dict(province={'H-K':[dict(code=15000000000,address='湖北省')]}, cities={
    '15000000000':{'15017000000':'恩施土家族苗族自治州'},
    '15017000000':{'15017001000':'恩施市','15017002000':'利川市'}})
INCOMPLETE = dict(provName='湖北省',cityName='恩施土家族苗族自治州',areaName=None,
                  provId=15000000000,cityId=15017000000,areaId=None,isLeafArea=False)


class OfficialRegionTests(unittest.TestCase):
    def test_incomplete_parser_resolves_exact_official_county_and_preserves_detail(self):
        with patch.object(address,'_request',return_value={'result':INCOMPLETE}), \
             patch.object(address,'_official_area_data',return_value=TREE):
            result=address._payload(None,RECEIVER)
        self.assertEqual(str(result['area']),'15017001000')
        self.assertEqual(result['address'],'测试街道测试路26号')
        self.assertEqual(result['receiverName'],RECEIVER['receiver_name'])
        self.assertEqual(result['mobile'],RECEIVER['receiver_mobile'])

    def test_same_county_name_in_other_province_cannot_match(self):
        wrong=copy.deepcopy(TREE)
        wrong['cities']['15017000000']={'15017002000':'利川市'}
        wrong['cities']['999999']={'999999001':'恩施市'}
        with patch.object(address,'_official_area_data',return_value=wrong):
            with self.assertRaises(address.AddressSyncError):address._official_region(None,RECEIVER)

    def test_prefix_and_missing_district_are_not_guessed(self):
        with patch.object(address,'_official_area_data',return_value=TREE):
            for receiver in (dict(RECEIVER,city_name='恩施州'),dict(RECEIVER,area_name='')):
                with self.assertRaises(address.AddressSyncError):address._official_region(None,receiver)

    def test_duplicate_official_match_is_rejected(self):
        duplicate=copy.deepcopy(TREE)
        duplicate['cities']['15017000000']['15017099999']='恩施市'
        with patch.object(address,'_official_area_data',return_value=duplicate):
            with self.assertRaises(address.AddressSyncError):address._official_region(None,RECEIVER)

    def test_municipality_and_city_leaf_share_existing_strict_check(self):
        tree=dict(province={'A-G':[dict(code=4000000000,address='重庆市'),dict(code=8000000000,address='广东省')]},
                  cities={'4000000000':{'4019000000':'涪陵区'},'8000000000':{'8017000000':'中山市'}})
        with patch.object(address,'_official_area_data',return_value=tree):
            for receiver,expected in ((dict(RECEIVER,prov_name='重庆',city_name='重庆市',area_name='涪陵区'),'4019000000'),
                                      (dict(RECEIVER,prov_name='广东省',city_name='中山市',area_name=''),'8017000000')):
                result=address._official_region(None,receiver)
                self.assertEqual(str(address._checked_region_id(result,receiver)),expected)

    def test_asset_is_json_only_and_get_uses_fixed_official_url(self):
        request=Mock()
        request.get.return_value=SimpleNamespace(ok=True,text=lambda:'var ___AREADATA = '+json.dumps({'status':1,'result':TREE})+';')
        self.assertEqual(address._official_area_data(SimpleNamespace(request=request)),TREE)
        request.get.assert_called_once_with('https://res.kongfz.com/js/data/area.js',timeout=12000,max_redirects=0)
        request.get.return_value=SimpleNamespace(ok=True,text=lambda:'var ___AREADATA = {}; executeUnexpectedCode()')
        with self.assertRaises(address.AddressSyncError):address._official_area_data(SimpleNamespace(request=request))

    def test_valid_parser_does_not_fetch_tree(self):
        parsed=dict(INCOMPLETE,areaName='恩施市',areaId=15017001000,isLeafArea=True)
        with patch.object(address,'_request',return_value={'result':parsed}),patch.object(address,'_official_area_data') as fallback:
            self.assertEqual(address._payload(None,RECEIVER)['area'],15017001000)
            fallback.assert_not_called()
