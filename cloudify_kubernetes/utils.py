########
# Copyright (c) 2017 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import yaml


from cloudify import ctx
from cloudify.workflows.workflow_context import CloudifyWorkflowNodeInstance

from .k8s import (KubernetesApiMapping,
                  KuberentesInvalidDefinitionError,
                  KuberentesMappingNotFoundError,
                  KubernetesResourceDefinition,
                  get_mapping)


NODE_PROPERTY_API_MAPPING = 'api_mapping'
NODE_PROPERTY_DEFINITION = 'definition'
NODE_PROPERTY_FILE = 'file'
NODE_PROPERTY_OPTIONS = 'options'


def _yaml_from_file(
        resource_path,
        target_path=None,
        template_variables=None):

    template_variables = template_variables or {}

    downloaded_file_path = \
        ctx.download_resource_and_render(
            resource_path,
            target_path,
            template_variables)

    with open(downloaded_file_path) as outfile:
        file_content = outfile.read()

    return yaml.load(file_content)


def get_ctx_from_kwargs(_kwargs, _attribute='instance'):

    _ctx = _kwargs.get('ctx') or ctx
    node_instance_id = _kwargs.get('node_instance_id')
    if node_instance_id:
        get_result = _ctx.get_node_instance(node_instance_id)
        if isinstance(get_result, CloudifyWorkflowNodeInstance):
            if _attribute == 'node':
                result = get_result.node
            else:
                result = get_result
        else:
            result = getattr(get_result, _attribute)
    else:
        result = getattr(_ctx, _attribute)
    return _ctx, result


def mapping_by_data(resource_definition, **kwargs):
    mapping_data = kwargs.get(
        NODE_PROPERTY_API_MAPPING,
        ctx.node.properties.get(NODE_PROPERTY_API_MAPPING, None)
    )

    if mapping_data:
        return KubernetesApiMapping(**mapping_data)

    raise KuberentesMappingNotFoundError(
        'Cannot find API mapping for this request - '
        '"api_mapping" property data is invalid'
    )


def mapping_by_kind(resource_definition, **kwargs):
    return get_mapping(kind=resource_definition.kind)


def get_definition_object(**kwargs):

    _ctx, node = get_ctx_from_kwargs(kwargs, _attribute='node')

    definition = kwargs.get(
        NODE_PROPERTY_DEFINITION,
        node.properties.get(NODE_PROPERTY_DEFINITION, None)
    )

    if not definition:
        raise KuberentesInvalidDefinitionError(
            'Incorrect format of resource definition'
        )

    if 'kind' not in definition:
        definition['kind'] = node.type \
            if isinstance(node.type, basestring)\
            else ''

    return definition


def resource_definition_from_blueprint(**kwargs):
    definition = get_definition_object(**kwargs)
    return KubernetesResourceDefinition(**definition)


def resource_definition_from_file(**kwargs):
    file_resource = kwargs.get(
        NODE_PROPERTY_FILE,
        ctx.node.properties.get(NODE_PROPERTY_FILE, None)
    )

    if not file_resource:
        raise KuberentesInvalidDefinitionError(
            'Invalid resource file definition'
        )

    return KubernetesResourceDefinition(
        **_yaml_from_file(**file_resource)
    )
