import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const root = fs.mkdtempSync(path.join(os.tmpdir(),'damodaran-source-addresses-'));
Object.assign(process.env,{APP_ROOT:root,DATA_DIR:path.join(root,'unused-data'),TRANSLATION_PROVIDER:'argos',OPENAI_API_KEY:''});
const {isPublicAddress} = await import('../lib/sources');
after(() => {
  assert.ok(path.resolve(root).startsWith(path.resolve(os.tmpdir()) + path.sep));
  assert.equal(fs.existsSync(path.join(root,'unused-data')),false);
  fs.rmSync(root,{recursive:true,force:true});
});

test('observed public NYU IPv4 addresses and well-known NAT64 compressed, expanded and dotted forms agree', () => {
  for(const address of ['128.122.198.17','23.185.0.1','8.8.8.8','64:ff9b::807a:c611','64:ff9b::17b9:1',
    '0064:FF9B:0000:0000:0000:0000:807A:C611','64:ff9b:0:0:0::17b9:1',
    '64:ff9b::128.122.198.17','0064:ff9b:0:0:0:0:23.185.0.1'])assert.equal(isPublicAddress(address),true,address);
});

test('NAT64 embedded private, loopback, reserved and documentation destinations retain IPv4 rejection', () => {
  const rejected = ['0.0.0.0','0.0.0.1','10.1.2.3','127.0.0.1','100.64.0.1','100.127.255.255','169.254.169.254',
    '172.16.0.1','172.31.255.255','192.168.1.1','192.0.0.1','192.0.2.1','198.18.0.1','198.19.255.255',
    '198.51.100.1','203.0.113.1','224.0.0.1','240.0.0.1','255.255.255.255'];
  for(const address of rejected){
    assert.equal(isPublicAddress(address),false,address);
    const [a,b,c,d] = address.split('.').map(Number),tail = `${((a<<8)|b).toString(16)}:${((c<<8)|d).toString(16)}`;
    for(const embedded of [`64:ff9b::${address}`,`64:ff9b::${tail}`,`0064:ff9b:0000:0000:0000:0000:${tail}`]){
      assert.equal(isPublicAddress(embedded),false,embedded);
    }
  }
  assert.equal(isPublicAddress('64:ff9b::1'),false);
  assert.equal(isPublicAddress('64:ff9b::'),false);
});

test('only the exact well-known /96 receives NAT64 treatment and malformed forms stay invalid', () => {
  for(const address of ['64:ff9b:1::808:808','64:ff9b:0:0:0:1:808:808','64:ff9a::808:808','65:ff9b::808:808',
    '64:ff9b::8.8.8.256','64:ff9b:::808:808','64:ff9b::808:808:808','64:ff9b::808:808%eth0',
    '[64:ff9b::808:808]','64:ff9b::808:808/96','64:ff9b::008.008.008.008'])assert.equal(isPublicAddress(address),false,address);
});

test('existing ordinary IPv6 and mapped IPv4 decisions are retained', () => {
  for(const address of ['2606:4700:4700::1111','2001:4860:4860::8888','2001:4860:4860:0:0:0:0:8888','::ffff:8.8.8.8']){
    assert.equal(isPublicAddress(address),true,address);
  }
  for(const address of ['::','::1','::ffff:127.0.0.1','fc00::1','fd00::1','fe80::1','ff02::1','2001:db8::1']){
    assert.equal(isPublicAddress(address),false,address);
  }
});
