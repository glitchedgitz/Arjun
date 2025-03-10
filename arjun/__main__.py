#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from arjun.core.colors import green, end, info, bad, good, run, res

import argparse
import sys

from urllib.parse import urlparse
import arjun.core.config as mem
from arjun.core.bruter import bruter
from arjun.core.exporter import exporter
from arjun.core.requester import requester
from arjun.core.anomaly import define
from arjun.core.utils import fetch_params, stable_request, random_str, slicer, confirm, populate, reader, nullify, prepare_requests, compatible_path

from arjun.plugins.heuristic import heuristic

arjun_dir = compatible_path(mem.__file__.replace(compatible_path('/core/config.py'), ''))

parser = argparse.ArgumentParser() # defines the parser
# Arguments that can be supplied
parser.add_argument('-u', help='Target URL', dest='url')
parser.add_argument('-o', '-oJ', help='Path for json output file.', dest='json_file')
parser.add_argument('-oT', help='Path for text output file.', dest='text_file')
parser.add_argument('-oB', help='Port for output to Burp Suite Proxy. Default port is 8080.', dest='burp_port', nargs='?', const=8080)
parser.add_argument('-d', help='Delay between requests in seconds. (default: 0)', dest='delay', type=float, default=0)
parser.add_argument('-t', help='Number of concurrent threads. (default: 2)', dest='threads', type=int, default=2)
parser.add_argument('-w', help='Wordlist file path. (default: {arjundir}/db/default.txt)', dest='wordlist', default=arjun_dir+'/db/default.txt')
parser.add_argument('-m', help='Request method to use: GET/POST/XML/JSON. (default: GET)', dest='method', default='GET')
parser.add_argument('-i', help='Import target URLs from file.', dest='import_file', nargs='?', const=True)
parser.add_argument('-T', help='HTTP request timeout in seconds. (default: 15)', dest='timeout', type=float, default=15)
parser.add_argument('-c', help='Chunk size. The number of parameters to be sent at once', type=int, dest='chunks', default=500)
parser.add_argument('-q', help='Quiet mode. No output.', dest='quiet', action='store_true')
parser.add_argument('--headers', help='Add headers. Separate multiple headers with a new line.', dest='headers', nargs='?', const=True)
parser.add_argument('--passive', help='Collect parameter names from passive sources like wayback, commoncrawl and otx.', dest='passive', nargs='?', const='-')
parser.add_argument('--stable', help='Prefer stability over speed.', dest='stable', action='store_true')
parser.add_argument('--include', help='Include this data in every request.', dest='include', default={})
args = parser.parse_args() # arguments to be parsed

if args.quiet:
    print = nullify

print('''%s    _
   /_| _ '
  (  |/ /(//) v%s
      _/      %s
''' % (green, __import__('arjun').__version__, end))

try:
    from concurrent.futures import ThreadPoolExecutor, as_completed
except ImportError:
    print('%s Please use Python > 3.2 to run Arjun.' % bad)
    quit()

mem.var = vars(args)

mem.var['method'] = mem.var['method'].upper()

if mem.var['stable'] or mem.var['delay']:
    mem.var['threads'] = 1

try:
    wordlist_file = arjun_dir + '/db/small.txt' if args.wordlist == 'small' else args.wordlist
    if wordlist_file == "-":  # read from stdin
        wordlist = [line.strip("\n") for line in sys.stdin] 
    else:
        wordlist_file = compatible_path(wordlist_file)
        wordlist = set(reader(wordlist_file, mode='lines'))
    if mem.var['passive']:
        host = mem.var['passive']
        if host == '-':
            host = urlparse(args.url).netloc
        print('%s Collecting parameter names from passive sources for %s, it may take a while' % (run, host))
        passive_params = fetch_params(host)
        wordlist.update(passive_params)
        print('%s Collected %s parameters, added to the wordlist' % (info, len(passive_params)))
    wordlist = list(wordlist)
except FileNotFoundError:
    exit('%s The specified file for parameters doesn\'t exist' % bad)

if len(wordlist) < mem.var['chunks']:
    mem.var['chunks'] = int(len(wordlist)/2)

if not args.url and not args.import_file:
    exit('%s No target(s) specified' % bad)


def narrower(request, factors, param_groups):
    """
    takes a list of parameters and narrows it down to parameters that cause anomalies
    returns list
    """
    anamolous_params = []
    threadpool = ThreadPoolExecutor(max_workers=mem.var['threads'])
    futures = (threadpool.submit(bruter, request, factors, params) for params in param_groups)
    for i, result in enumerate(as_completed(futures)):
        if result.result():
            anamolous_params.extend(slicer(result.result()))
        if not mem.var['kill']:
            print('%s Processing chunks: %i/%-6i' % (info, i + 1, len(param_groups)), end='\r')
    return anamolous_params


def initialize(request, wordlist):
    """
    handles parameter finding process for a single request object
    returns 'skipped' (on error), list on success
    """
    url = request['url']
    if not url.startswith('http'):
        print('%s %s is not a valid URL' % (bad, url))
        return 'skipped'
    print('%s Probing the target for stability' % run)
    stable = stable_request(url, request['headers'])
    if not stable:
        return 'skipped'
    else:
        fuzz = random_str(6)
        response_1 = requester(request, {fuzz: fuzz[::-1]})
        print('%s Analysing HTTP response for anamolies' % run)
        fuzz = random_str(6)
        response_2 = requester(request, {fuzz: fuzz[::-1]})
        if type(response_1) == str or type(response_2) == str:
            return 'skipped'
        factors = define(response_1, response_2, fuzz, fuzz[::-1], wordlist)
        print('%s Analysing HTTP response for potential parameter names' % run)
        found = heuristic(response_1.text, wordlist)
        if found:
            num = len(found)
            s = 's' if num > 1 else ''
            print('%s Heuristic scanner found %i parameter%s: %s' % (good, num, s, ', '.join(found)))
        print('%s Logicforcing the URL endpoint' % run)
        populated = populate(wordlist)
        param_groups = slicer(populated, int(len(wordlist)/mem.var['chunks']))
        last_params = []
        while True:
            param_groups = narrower(request, factors, param_groups)
            if mem.var['kill']:
                return 'skipped'
            param_groups = confirm(param_groups, last_params)
            if not param_groups:
                break
        confirmed_params = []
        for param in last_params:
            reason = bruter(request, factors, param, mode='verify')
            if reason:
                name = list(param.keys())[0]
                confirmed_params.append(name)
                print('%s name: %s, factor: %s' % (res, name, reason))
        return confirmed_params


def main():
    request = prepare_requests(args)

    final_result = {}

    try:
        if type(request) == dict:
            # in case of a single target
            mem.var['kill'] = False
            url = request['url']
            these_params = initialize(request, wordlist)
            if these_params == 'skipped':
                print('%s Skipped %s due to errors' % (bad, request['url']))
            elif these_params:
                final_result[url] = {}
                final_result[url]['params'] = these_params
                final_result[url]['method'] = request['method']
                final_result[url]['headers'] = request['headers']
        elif type(request) == list:
            # in case of multiple targets
            for each in request:
                url = each['url']
                mem.var['kill'] = False
                print('%s Scanning: %s' % (run, url))
                these_params = initialize(each, list(wordlist))
                if these_params == 'skipped':
                    print('%s Skipped %s due to errors' % (bad, url))
                elif these_params:
                    final_result[url] = {}
                    final_result[url]['params'] = these_params
                    final_result[url]['method'] = each['method']
                    final_result[url]['headers'] = each['headers']
                    print('%s Parameters found: %s' % (good, ', '.join(final_result[url])))
    except KeyboardInterrupt:
        exit()

    exporter(final_result)


if __name__ == '__main__':
    main()
