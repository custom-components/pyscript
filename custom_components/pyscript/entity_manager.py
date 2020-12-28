class EntityManager:

    hass = None

    platform_adders = {}
    platform_classes = {}
    registered_entities = {}


    @classmethod
    def init(cls, hass):
        cls.hass = hass

    @classmethod
    def register_platform(cls, platform, adder, entity_class):
        cls.platform_adders[platform] = adder
        cls.platform_classes[platform] = entity_class
        cls.registered_entities[platform] = {}

    @classmethod
    def get(cls, platform, name):
        if platform not in cls.registered_entities or name not in cls.registered_entities[platform]:
            cls.create(platform, name)

        return cls.registered_entities[platform][name]

    @classmethod
    def create(cls, platform, name):
        new_entity = cls.platform_classes[platform](cls.hass, name)
        cls.platform_adders[platform]([new_entity])
        cls.registered_entities[platform][name] = new_entity
        