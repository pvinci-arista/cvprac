from cvprac.cvp_client import CvpClient
import os
import re
import sys
import time
import uuid
from pprint import pprint

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


sys.path.append(os.path.join(os.path.dirname(__file__), '../lib'))
from systestlib import DutSystemTest

class TestCvpClientBase(DutSystemTest):
    ''' Test cases for the CvpClient class.
    '''
    # pylint: disable=too-many-public-methods
    # pylint: disable=invalid-name
    @classmethod
    def setUpClass(cls):
        ''' Instantiate the CvpClient class and connect to the CVP node.
            Log messages to the /tmp/TestCvpClient.log
        '''
        super(TestCvpClientBase, cls).setUpClass()
        cls.clnt = CvpClient(filename='/tmp/TestCvpClient.log')
        assert cls.clnt is not None
        assert cls.clnt.last_used_node is None
        dut = cls.duts[0]
        cert = dut.get("cert", False)
        username = dut.get("username", "")
        password = dut.get("password", "")
        api_token = dut.get("api_token", None)
        is_cvaas = dut.get("is_cvaas", False)

        cls.clnt.connect([dut['node']], username, password, 10,
                         cert=cert, is_cvaas=is_cvaas, api_token=api_token)

        cls.api = cls.clnt.api
        assert cls.api is not None

        # Verify that there is at least one device in the inventory
        err_msg = 'CVP node must contain at least one device'
        result = cls.api.get_inventory()
        assert result is not None
        if len(result) < 1:
            raise AssertionError(err_msg)
        else:
            assert len(result) >= 1
        device = [res for res in result if res['hostname']
                  == dut.get("device", "")]
        # cls.device = result[0]
        cls.device = device[0]
        # Get the container for the device on the list and
        # use that container as the parent container.
        result = cls.api.search_topology(cls.device['fqdn'])
        assert result is not None
        dev_container = result['netElementContainerList']
        assert len(dev_container) >= 1
        info = dev_container[0]
        result = cls.api.search_topology(info['containerName'])
        assert result is not None
        cls.container = result['containerList'][0]

        # Get the configlets assigned to the device.  There must be at least 1.
        err_msg = 'CVP node device must have at least one configlet assigned'
        key = info['netElementKey']
        result = cls.api.get_configlets_by_device_id(key)
        if len(result) < 1:
            raise AssertionError(err_msg)
        cls.dev_configlets = result

    @classmethod
    def tearDownClass(cls):
        ''' Destroy the CvpClient class.
        '''
        super(TestCvpClientBase, cls).tearDownClass()
        cls.api = None
        cls.clnt = None

    def cancel_task(self, task_id):
        """ Cancel task
        """
        pprint('CANCELING TASK...')
        data = {'data': [task_id]}
        self.clnt.post('/task/cancelTask.do', data=data)

    def setUp(self):
        self.task_id = None
        self.cc_id = str(uuid.uuid4())
        self.cc_name = 'test_api_%d' % time.time()

    def tearDown(self):
        chg_ctrl_get_one = self.api.change_control_get_one(self.cc_id)
        if chg_ctrl_get_one:
            # Delete CC
            self.delete_change_control(self.cc_id)
            # Cancel task
            if self.task_id:
                task = self.api.get_task_by_id(self.task_id)
                if task and task['currentTaskName'] != "Cancelled":
                    self.cancel_task(self.task_id)

    def _get_next_task_id(self):
        ''' Return the next task id.

            Returns:
                task_id (str): Task ID
        '''
        # Get all the tasks and the task id of the next task created is
        # the length + 1.
        results = self.api.get_tasks()
        self.assertIsNotNone(results)
        latest_task = str(int(results['data'][0]['workOrderId']) + 1)
        total_task = str(results['total'])
        return latest_task

    def _create_task(self):
        ''' Create a task by making a simple change to a configlet assigned
            to the device.

            Returns:
                (task_id, config)
                task_id (str): Task ID
                config (str): Previous configlets contents
        '''
        task_id = self._get_next_task_id()
        # Update the lldp time in the first configlet in the list.
        configlet = None
        for conf in self.dev_configlets:
            if conf['netElementCount'] == 1:
                configlet = conf
                break
        if configlet is None:
            configlet = self.dev_configlets[0]

        config = configlet['config']
        org_config = config

        match = re.match(r'lldp timer (\d+)', config)
        if match is not None:
            value = int(match.group(1)) + 1
            repl = 'lldp timer %d' % value
            config = re.sub(match.group(0), repl, config)
        else:
            value = 13
            config = ('lldp timer %d\n' % value) + config
        configlet['config'] = config

        # Updating the configlet will cause a task to be created to apply
        # the change to the device.
        self.api.update_configlet(config, configlet['key'], configlet['name'])

        # Wait 30 seconds for task to get created
        cnt = 30
        if self.clnt.apiversion is None:
            self.api.get_cvp_info()
        if self.clnt.apiversion >= 2.0:
            # Increase timeout by 30 sec for CVP 2018.2 and beyond
            cnt += 30
        while cnt > 0:
            time.sleep(1)
            result = self.api.get_task_by_id(task_id)
            if result is not None:
                break
            cnt -= 1
        err_msg = 'Timeout waiting for task id %s to be created' % task_id
        self.assertGreater(cnt, 0, msg=err_msg)
        self.task_id = task_id
        return task_id, org_config
