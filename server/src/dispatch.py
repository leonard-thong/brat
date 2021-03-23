#!/usr/bin/env python
# -*- Mode: Python; tab-width: 4; indent-tabs-mode: nil; coding: utf-8; -*-
# vim:set ft=python ts=4 sw=4 sts=4 autoindent:

"""Server request dispatching mechanism.

Author:     Pontus Stenetorp    <pontus is s u-tokyo ac jp>
Version:    2011-04-21
"""

from inspect import getargspec
from logging import info as log_info
from os.path import join as path_join
from os.path import abspath, normpath

from config import DATA_DIR

from annlog import log_annotation
from annotator import (create_arc, create_span, delete_arc, delete_span,
                       reverse_arc, split_span)
from auth import NotAuthorisedError, login, logout, whoami, create_new_user
from common import ProtocolError
from convert.convert import convert
from delete import delete_collection, delete_document
from docimport import save_import
from document import (get_configuration, get_directory_information,
                      get_document, get_document_timestamp)
from download import download_collection, download_file
from jsonwrap import dumps
from message import Messager
from norm import norm_get_data, norm_get_name, norm_search
from predict import suggest_span_types
from search import (search_entity, search_event, search_note, search_relation,
                    search_text)
from session import get_session, load_conf, save_conf
from svg import retrieve_stored, store_svg
from tag import tag
from undo import undo
from labelFunctionExecutor import function_executor, instant_executor
from dynamicLabeling import add_labeling_function, delete_labeling_function, get_available_labeling_function
from newDocument import create_new_document, import_new_document, delete_new_document, create_folder
from createSpanAll import create_span_all_text, create_span_all_re
from utils import GLOBAL_LOGGER, fetch_all_annotations, prehandle_data, cache_model_results
from newEntity import create_new_entity
# no-op function that can be invoked by client to log a user action


def logging_no_op(collection, document, log):
    # need to return a dictionary
    return {}


# Constants
# Function call-backs
DISPATCHER = {
    'getCollectionInformation': get_directory_information,
    'getDocument': get_document,
    'getDocumentTimestamp': get_document_timestamp,
    'importDocument': save_import,

    'storeSVG': store_svg,
    'retrieveStored': retrieve_stored,
    'downloadFile': download_file,
    'downloadCollection': download_collection,

    'login': login,
    'logout': logout,
    'whoami': whoami,
    'createNewUser': create_new_user,

    'createSpan': create_span,
    'deleteSpan': delete_span,
    'splitSpan': split_span,

    'createEntity': create_new_entity,

    'createArc': create_arc,
    'reverseArc': reverse_arc,
    'deleteArc': delete_arc,

    # NOTE: search actions are redundant to allow different
    # permissions for single-document and whole-collection search.
    'searchTextInDocument': search_text,
    'searchEntityInDocument': search_entity,
    'searchEventInDocument': search_event,
    'searchRelationInDocument': search_relation,
    'searchNoteInDocument': search_note,
    'searchTextInCollection': search_text,
    'searchEntityInCollection': search_entity,
    'searchEventInCollection': search_event,
    'searchRelationInCollection': search_relation,
    'searchNoteInCollection': search_note,

    'suggestSpanTypes': suggest_span_types,

    'logAnnotatorAction': logging_no_op,

    'saveConf': save_conf,
    'loadConf': load_conf,

    'undo': undo,
    'tag': tag,

    'deleteDocument': delete_document,
    'deleteCollection': delete_collection,

    # normalization support
    'normGetName': norm_get_name,
    'normSearch': norm_search,
    'normData': norm_get_data,

    # Visualisation support
    # This interface has been abandoned, just ignore it.
    'getConfiguration': get_configuration,
    'convert': convert,
    'labelingFunctionProcess': function_executor,
    'instantExecutor': instant_executor,
    'addLabelingFunction': add_labeling_function,
    'deleteLabelingFunction': delete_labeling_function,
    'getAvailableLabelingFunction': get_available_labeling_function,
    'fetchAllAnnotations': fetch_all_annotations,

    'createNewDocument': create_new_document,
    'importNewDocument': import_new_document,
    'deleteNewDocument':delete_new_document,
    'createFolder': create_folder,

    'createSpanAllText': create_span_all_text,
    'createSpanAllRe': create_span_all_re,
    'preprocessModelData': prehandle_data,
    'cacheModelResults': cache_model_results
}

# Actions that correspond to labeling function functionality
EXPAND_ACTION = {'labelingFunctionProcess', 'instantExecutor', 'addLabelingFunction', 'deleteLabelingFunction', 'getAvailableLabelingFunction', 'createSpanAllText', 
                    'createSpanAllRe', 'fetchAllAnnotations', 'preprocessModelData', 'createNewDocument', 'importNewDocument','deleteNewDocument', 'createNewUser', 'cacheModelResults', 'createFolder', 'createEntity'}

# Actions that correspond to annotation functionality
ANNOTATION_ACTION = {'createArc', 'deleteArc', 'createSpan', 'deleteSpan', 'splitSpan', 'suggestSpanTypes', 'undo'}

# Actions that will be logged as annotator actions (if so configured)
LOGGED_ANNOTATOR_ACTION = ANNOTATION_ACTION | {'getDocument', 'logAnnotatorAction'}

# Actions that require authentication
REQUIRES_AUTHENTICATION = ANNOTATION_ACTION | {'importDocument', 'searchTextInCollection', 'searchEntityInCollection', 'searchEventInCollection', 'searchRelationInCollection', 
                    'searchNoteInCollection', 'tag'}

# Sanity check
for req_action in REQUIRES_AUTHENTICATION:
    assert req_action in DISPATCHER, (
        'INTERNAL ERROR: undefined action in REQUIRES_AUTHENTICATION set')
###


class NoActionError(ProtocolError):
    def __init__(self):
        pass

    def __str__(self):
        return 'Client sent no action for request'

    def json(self, json_dic):
        json_dic['exception'] = 'noAction'
        return json_dic


class InvalidActionError(ProtocolError):
    def __init__(self, attempted_action):
        self.attempted_action = attempted_action

    def __str__(self):
        return 'Client sent an invalid action "%s"' % self.attempted_action

    def json(self, json_dic):
        json_dic['exception'] = 'invalidAction',
        return json_dic


class InvalidActionArgsError(ProtocolError):
    def __init__(self, attempted_action, missing_arg):
        self.attempted_action = attempted_action
        self.missing_arg = missing_arg

    def __str__(self):
        return 'Client did not supply argument "%s" for action "%s"' % (
            self.missing_arg, self.attempted_action)

    def json(self, json_dic):
        json_dic['exception'] = 'invalidActionArgs',
        return json_dic


class DirectorySecurityError(ProtocolError):
    def __init__(self, requested):
        self.requested = requested

    def __str__(self):
        return 'Client sent request for bad directory: ' + self.requested

    def json(self, json_dic):
        json_dic['exception'] = 'directorySecurity',
        return json_dic


class ProtocolVersionMismatchError(ProtocolError):
    def __init__(self, was, correct):
        self.was = was
        self.correct = correct

    def __str__(self):
        return '\n'.join((
            ('Client-server mismatch, please reload the page to update your '
                'client. If this does not work, please contact your '
                'administrator'),
            ('Client sent request with version "%s", server is using version '
                '%s') % (self.was, self.correct, ),
        ))

    def json(self, json_dic):
        json_dic['exception'] = 'protocolVersionMismatch',
        return json_dic


def _directory_is_safe(dir_path):
    # TODO: Make this less naive
    if not dir_path.startswith('/'):
        # We only accept absolute paths in the data directory
        return False

    # Make a simple test that the directory is inside the data directory
    return abspath(path_join(DATA_DIR, dir_path[1:])
                   ).startswith(normpath(DATA_DIR))


def dispatch(http_args, client_ip, client_hostname):
    action = http_args['action']
    log_info('dispatcher handling action: %s' % (action, ))
    GLOBAL_LOGGER.log_normal(http_args.__str__())

    # Verify that we don't have a protocol version mismatch
    PROTOCOL_VERSION = 1
    try:
        protocol_version = int(http_args['protocol'])
        if protocol_version != PROTOCOL_VERSION:
            raise ProtocolVersionMismatchError(protocol_version,
                                               PROTOCOL_VERSION)
    except TypeError:
        #raise ProtocolVersionMismatchError('None', PROTOCOL_VERSION)
        pass
    except ValueError:
        #raise ProtocolVersionMismatchError(http_args['protocol'],
        #                                   PROTOCOL_VERSION)
        pass

    # Was an action supplied?
    if action is None:
        raise NoActionError

    # If we got a directory (collection), check it for security
    if http_args['collection'] is not None:
        if not _directory_is_safe(http_args['collection']):
            raise DirectorySecurityError(http_args['collection'])

    # Make sure that we are authenticated if we are to do certain actions
    if action in REQUIRES_AUTHENTICATION:
        try:
            user = get_session()['user']
        except KeyError:
            user = None
        if user is None:
            log_info('Authorization failure for "%s" with hostname "%s"'
                     % (client_ip, client_hostname))
            raise NotAuthorisedError(action)

    # Fetch the action function for this action (if any)
    try:
        action_function = DISPATCHER[action]
    except KeyError:
        log_info('Invalid action "%s"' % action)
        raise InvalidActionError(action)

    if action in EXPAND_ACTION:
        json_dic = action_function(**http_args)

        # Assign which action that was performed to the json_dic
        json_dic['action'] = action
        # Return the protocol version for symmetry
        json_dic['protocol'] = PROTOCOL_VERSION
        json_dic['comments'] = []
        # GLOBAL_LOGGER.log_error(json_dic.__str__())
        return json_dic

    # Determine what arguments the action function expects
    args, varargs, keywords, defaults = getargspec(action_function)
    # We will not allow this for now, there is most likely no need for it
    assert varargs is None, 'no varargs for action functions'
    assert keywords is None, 'no keywords for action functions'

    # XXX: Quick hack
    if defaults is None:
        defaults = []

    # These arguments already has default values
    default_val_by_arg = {}
    for arg, default_val in zip(args[-len(defaults):], defaults):
        default_val_by_arg[arg] = default_val

    action_args = []
    for arg_name in args:
        arg_val = http_args[arg_name]

        # The client failed to provide this argument
        if arg_val is None:
            try:
                arg_val = default_val_by_arg[arg_name]
            except KeyError:
                raise InvalidActionArgsError(action, arg_name)

        action_args.append(arg_val)

    log_info('dispatcher will call %s(%s)' %
             (action, ', '.join((repr(a) for a in action_args)), ))

    # Log annotation actions separately (if so configured)
    if action in LOGGED_ANNOTATOR_ACTION:
        log_annotation(http_args['collection'],
                       http_args['document'],
                       'START', action, action_args)

    # TODO: log_annotation for exceptions?

    json_dic = action_function(*action_args)

    # Log annotation actions separately (if so configured)
    if action in LOGGED_ANNOTATOR_ACTION:
        log_annotation(http_args['collection'],
                       http_args['document'],
                       'FINISH', action, action_args)
    # GLOBAL_LOGGER.log_error(json_dic.__str__())
    # # Assign which action that was performed to the json_dic
    # json_dic['entities'] = [['T1', 'Entity', [(0, 6)]], ['T2', 'PPP', [(381, 387)]], ['T3', 'Protein', [(401, 413)]], ['T4', 'Protein', [(639, 645)]], ['T5', 'Protein', [(1190, 1202)]], ['T6', 'Protein', [(1254, 1263)]], ['T7', 'Protein', [(1357, 1366)]], ['T8', 'Protein', [(1367, 1376)]], ['T9', 'Protein', [(1420, 1429)]], ['T10', 'Protein', [(1455, 1461)]], ['T11', 'Protein', [(1562, 1571)]]]
    # json_dic['events'] = [['E1', 'T1', [('Theme', 'T7')]], ['E2', 'T2', [('Theme', 'T8')]]]
    # json_dic['triggers'] = [['T1', 'Protein', [(0, 6)]], ['T2', 'PPP', [(381, 387)]]]
    json_dic['comments'] = []
    if 'annotations' in json_dic.keys():
        json_dic['annotations']['comments'] = []
        del json_dic['annotations']['sentence_offsets']

    json_dic['action'] = action
    # Return the protocol version for symmetry
    json_dic['protocol'] = PROTOCOL_VERSION
    return json_dic
