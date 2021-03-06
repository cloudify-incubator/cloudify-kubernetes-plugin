# Copyright (c) 2017-2019 Cloudify Platform Ltd. All rights reserved
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

from cloudify import ctx
from cloudify.exceptions import (
    RecoverableError,
    NonRecoverableError
)
from cloudify.decorators import operation

from ._compat import text_type
from .utils import (get_node,
                    get_instance,
                    retrieve_path,
                    NODE_PROPERTY_FILE,
                    handle_existing_resource,
                    generate_traceback_exception,
                    NODE_PROPERTY_FILE_RESOURCE_PATH,
                    create_tempfiles_for_certs_and_keys,
                    delete_tempfiles_for_certs_and_keys,
                    INSTANCE_RUNTIME_PROPERTY_KUBERNETES)
from .k8s import (CloudifyKubernetesClient,
                  KuberentesMappingNotFoundError,
                  KuberentesInvalidApiClassError,
                  KuberentesInvalidApiMethodError,
                  KubernetesApiConfigurationVariants,
                  KuberentesInvalidPayloadClassError,
                  KubernetesApiAuthenticationVariants)

NODE_PROPERTY_AUTHENTICATION = 'authentication'
NODE_PROPERTY_CONFIGURATION = 'configuration'
RELATIONSHIP_TYPE_MANAGED_BY_MASTER = (
    'cloudify.kubernetes.relationships.managed_by_master'
)


def _retrieve_master(resource_instance):
    for relationship in resource_instance.relationships:
        if relationship.type == RELATIONSHIP_TYPE_MANAGED_BY_MASTER:
            return relationship.target


def _retrieve_property(_ctx, property_name, client_config=None):
    client_config = client_config or get_node(_ctx).properties.get(
        'client_config', {})
    property_from_client_config = client_config.get(property_name, {})
    target = _retrieve_master(get_instance(_ctx))

    if target:
        _ctx.logger.info("using property from managed_by_master"
                         " relationship for node: {0}, it will be deprecated"
                         " soon please use client_config property!"
                         .format(_ctx.node.name))
        configuration = target.node.properties.get(property_name, {})
        configuration.update(
            target.instance.runtime_properties.get(property_name, {})
        )

    else:
        configuration = property_from_client_config
        configuration.update(
            get_instance(_ctx).runtime_properties.get(property_name, {}))

    return configuration


def _multidefinition_resource_task(task, definitions, kwargs,
                                   retrieve_mapping,
                                   cleanup_runtime_properties=False,
                                   resource_state_function=None):
    curr_num = 0
    # we have several definitions (not one!)
    multicalls = len(definitions) > 1
    # we can have several resources in one file, save origin
    origin_path = None
    if NODE_PROPERTY_FILE in kwargs and multicalls:
        # save original path only in case multicalls
        origin_path = kwargs[
            NODE_PROPERTY_FILE].get(NODE_PROPERTY_FILE_RESOURCE_PATH)
    elif NODE_PROPERTY_FILE in ctx.node.properties:
        # copy origin file name to kwargs
        kwargs[NODE_PROPERTY_FILE] = ctx.node.properties[NODE_PROPERTY_FILE]
        # save origin path
        origin_path = kwargs[
            NODE_PROPERTY_FILE].get(NODE_PROPERTY_FILE_RESOURCE_PATH)
    # iterate by definitions list
    for definition in definitions:
        kwargs['resource_definition'] = definition
        if retrieve_mapping:
            kwargs['api_mapping'] = retrieve_mapping(**kwargs)
        # we can have several resources in one file
        if origin_path:
            kwargs[NODE_PROPERTY_FILE][NODE_PROPERTY_FILE_RESOURCE_PATH] = (
                "{name}#{curr_num}".format(
                    name=origin_path,
                    curr_num=text_type(curr_num)
                ))
            curr_num += 1

        # check current state
        path = retrieve_path(kwargs)
        resource_id = definition.metadata.get('name')
        if resource_state_function and resource_id:
            current_state = resource_state_function(**kwargs)
        elif path:
            current_state = ctx.instance.runtime_properties.get(
                INSTANCE_RUNTIME_PROPERTY_KUBERNETES, {}).get(path)
        else:
            current_state = ctx.instance.runtime_properties.get(
                INSTANCE_RUNTIME_PROPERTY_KUBERNETES)

        handle_existing_resource(current_state, definition)
        # ignore pre-existing state
        task(**kwargs)
        del ctx.instance.runtime_properties['__perform_task']
        # cleanup after successful run
        if current_state and cleanup_runtime_properties:
            if path and path in ctx.instance.runtime_properties.get(
                    INSTANCE_RUNTIME_PROPERTY_KUBERNETES, {}):
                del ctx.instance.runtime_properties[
                    INSTANCE_RUNTIME_PROPERTY_KUBERNETES][path]
            else:
                ctx.instance.runtime_properties[
                    INSTANCE_RUNTIME_PROPERTY_KUBERNETES] = {}
            # remove empty kubernetes property
            if not ctx.instance.runtime_properties[
                INSTANCE_RUNTIME_PROPERTY_KUBERNETES
            ]:
                del ctx.instance.runtime_properties[
                    INSTANCE_RUNTIME_PROPERTY_KUBERNETES]
            # force save
            ctx.instance.runtime_properties.dirty = True
            ctx.instance.update()


def resource_task(retrieve_resource_definition=None,
                  retrieve_resources_definitions=None,
                  retrieve_mapping=None,
                  cleanup_runtime_properties=False,
                  resource_state_function=None):
    def decorator(task, **_):
        def wrapper(**kwargs):
            try:
                definitions = []
                # use single definition source
                if retrieve_resource_definition:
                    definitions = [retrieve_resource_definition(**kwargs)]
                # use multi definition source
                elif retrieve_resources_definitions:
                    definitions = retrieve_resources_definitions(**kwargs)
                # apply definition
                _multidefinition_resource_task(
                    task, definitions, kwargs, retrieve_mapping,
                    cleanup_runtime_properties=cleanup_runtime_properties,
                    resource_state_function=resource_state_function
                )
            except (KuberentesMappingNotFoundError,
                    KuberentesInvalidPayloadClassError,
                    KuberentesInvalidApiClassError,
                    KuberentesInvalidApiMethodError):
                raise NonRecoverableError(
                    'Kubernetes error encountered',
                    causes=[generate_traceback_exception()]
                )
            except (RecoverableError, NonRecoverableError):
                raise
            except Exception:
                raise RecoverableError(
                    'Error encountered',
                    causes=[generate_traceback_exception()]
                )

        return wrapper

    return decorator


def with_kubernetes_client(fn):
    def wrapper(**kwargs):
        client_config_from_inputs = kwargs.get('client_config')
        configuration_property = _retrieve_property(
            ctx,
            NODE_PROPERTY_CONFIGURATION,
            client_config_from_inputs
        )

        authentication_property = _retrieve_property(
            ctx,
            NODE_PROPERTY_AUTHENTICATION,
            client_config_from_inputs
        )

        configuration_property = create_tempfiles_for_certs_and_keys(
            configuration_property)

        try:
            kwargs['client'] = CloudifyKubernetesClient(
                ctx.logger,
                KubernetesApiConfigurationVariants(
                    ctx.logger,
                    configuration_property,
                    download_resource=ctx.download_resource
                ),
                KubernetesApiAuthenticationVariants(
                    ctx.logger,
                    authentication_property
                )
            )

            fn(**kwargs)
        except (RecoverableError, NonRecoverableError):
            raise
        except Exception:
            raise RecoverableError(
                'Error encountered',
                causes=[generate_traceback_exception()]
            )
        finally:
            delete_tempfiles_for_certs_and_keys(configuration_property)

    return operation(func=wrapper, resumable=True)
